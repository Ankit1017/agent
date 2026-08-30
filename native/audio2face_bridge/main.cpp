// SPDX-License-Identifier: MIT
// A bounded command-line bridge from canonical WAV audio to Audio2Face rig controls.

#include "AudioFile.h"
#include "audio2face/audio2face.h"
#include "audio2x/cuda_utils.h"
#include "audio2x/tensor_float.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

namespace {

struct Destroyer {
  template <typename T> void operator()(T* value) const { if (value != nullptr) value->Destroy(); }
};
template <typename T> using UniquePtr = std::unique_ptr<T, Destroyer>;

struct Arguments { std::string input; std::string model; std::string output; int fps{60}; };
struct RigResult {
  std::vector<std::string> face_controls;
  std::vector<std::string> tongue_controls;
  std::vector<std::vector<float>> weights;
};

void Check(std::error_code error, std::string_view operation) {
  if (error) throw std::runtime_error(std::string(operation));
}

Arguments ParseArguments(int argc, char** argv) {
  Arguments value;
  for (int index = 1; index < argc; index += 2) {
    if (index + 1 >= argc) throw std::runtime_error("missing argument value");
    const std::string key = argv[index];
    const std::string item = argv[index + 1];
    if (key == "--input") value.input = item;
    else if (key == "--model") value.model = item;
    else if (key == "--output") value.output = item;
    else if (key == "--fps") value.fps = std::stoi(item);
    else throw std::runtime_error("unknown argument");
  }
  if (value.input.empty() || value.model.empty() || value.output.empty() || value.fps != 60) {
    throw std::runtime_error("invalid arguments");
  }
  return value;
}

std::vector<float> ReadAudio(const std::string& path) {
  AudioFile<float> file;
  if (!file.load(path) || file.getSampleRate() != 16000 || file.getNumChannels() != 1) {
    throw std::runtime_error("input must be mono 16 kHz WAV");
  }
  if (file.samples.empty() || file.samples.front().empty() || file.samples.front().size() > 960000) {
    throw std::runtime_error("input duration is invalid");
  }
  return file.samples.front();
}

void AddNeutralEmotion(nva2f::IBlendshapeExecutorBundle& bundle) {
  auto& accumulator = bundle.GetEmotionAccumulator(0);
  std::vector<float> neutral(accumulator.GetEmotionSize(), 0.0F);
  Check(accumulator.Accumulate(
            0, nva2x::HostTensorFloatConstView{neutral.data(), neutral.size()},
            bundle.GetCudaStream().Data()), "accumulate emotion");
  Check(accumulator.Close(), "close emotion");
}

std::vector<std::string> PoseNames(nva2f::IBlendshapeSolver* solver) {
  std::vector<std::string> names;
  if (solver == nullptr) return names;
  const auto count = solver->NumBlendshapePoses();
  if (count < 0) throw std::runtime_error("invalid pose count");
  names.reserve(static_cast<std::size_t>(count));
  for (int index = 0; index < count; ++index) {
    const char* name = solver->GetPoseName(static_cast<std::size_t>(index));
    if (name == nullptr || *name == '\0') throw std::runtime_error("invalid pose name");
    names.emplace_back(name);
  }
  return names;
}

RigResult Generate(const std::vector<float>& audio, const std::string& model, int fps) {
  UniquePtr<nva2f::IBlendshapeExecutorBundle> bundle(
      nva2f::ReadRegressionBlendshapeSolveExecutorBundle(
          1, model.c_str(), nva2f::IGeometryExecutor::ExecutionOption::All,
          false, fps, 1, nullptr, nullptr));
  if (!bundle) throw std::runtime_error("model could not be loaded");
  AddNeutralEmotion(*bundle);
  auto& executor = bundle->GetExecutor();
  nva2f::IBlendshapeSolver* skin_solver = nullptr;
  nva2f::IBlendshapeSolver* tongue_solver = nullptr;
  Check(nva2f::GetExecutorSkinSolver(executor, 0, &skin_solver), "read skin solver");
  // Some licensed Mark packages contain no tongue solver. Skin controls remain
  // mandatory, while a missing tongue component is a supported model variant.
  if (nva2f::GetExecutorTongueSolver(executor, 0, &tongue_solver)) {
    tongue_solver = nullptr;
  }
  RigResult result{PoseNames(skin_solver), PoseNames(tongue_solver), {}};
  if (result.face_controls.size() != 52) throw std::runtime_error("model must provide 52 face controls");
  const std::size_t width = result.face_controls.size() + result.tongue_controls.size();
  if (width != executor.GetWeightCount()) throw std::runtime_error("model weight count is inconsistent");

  struct CallbackData {
    std::mutex mutex;
    std::vector<std::vector<float>> frames;
    bool failed{false};
    std::size_t width{0};
  } callback_data;
  callback_data.width = width;
  auto callback = [](
      void* opaque, const nva2f::IBlendshapeExecutor::HostResults& results,
      std::error_code error) {
    auto& data = *static_cast<CallbackData*>(opaque);
    std::scoped_lock lock(data.mutex);
    if (error || results.weights.Size() != data.width) { data.failed = true; return; }
    std::vector<float> frame(data.width, 0.0F);
    for (std::size_t index = 0; index < data.width; ++index) {
      const float value = results.weights.Data()[index];
      if (!std::isfinite(value)) { data.failed = true; return; }
      frame[index] = std::clamp(value, 0.0F, 1.0F);
    }
    data.frames.emplace_back(std::move(frame));
  };
  Check(executor.SetResultsCallback(callback, &callback_data), "set blendshape callback");
  Check(bundle->GetAudioAccumulator(0).Accumulate(
            nva2x::HostTensorFloatConstView{audio.data(), audio.size()},
            bundle->GetCudaStream().Data()), "accumulate audio");
  Check(bundle->GetAudioAccumulator(0).Close(), "close audio");
  while (nva2x::GetNbReadyTracks(executor) > 0) Check(executor.Execute(nullptr), "execute model");
  Check(executor.Wait(0), "wait for blendshape solve");
  {
    std::scoped_lock lock(callback_data.mutex);
    if (callback_data.failed || callback_data.frames.empty()) {
      throw std::runtime_error("model returned invalid controls");
    }
    result.weights = std::move(callback_data.frames);
  }
  return result;
}

std::size_t ControlIndex(const std::vector<std::string>& names, const std::string& name) {
  const auto found = std::find(names.begin(), names.end(), name);
  if (found == names.end()) throw std::runtime_error("required face control is missing");
  return static_cast<std::size_t>(std::distance(names.begin(), found));
}
float Weight(const std::vector<float>& frame, std::size_t index) {
  return index < frame.size() ? frame[index] : 0.0F;
}

void WriteFloatLittleEndian(std::ofstream& stream, float value) {
  std::uint32_t bits = 0;
  static_assert(sizeof(bits) == sizeof(value), "float32 output is required");
  std::memcpy(&bits, &value, sizeof(bits));
  const unsigned char bytes[4] = {
      static_cast<unsigned char>(bits & 0xFFU),
      static_cast<unsigned char>((bits >> 8U) & 0xFFU),
      static_cast<unsigned char>((bits >> 16U) & 0xFFU),
      static_cast<unsigned char>((bits >> 24U) & 0xFFU)};
  stream.write(reinterpret_cast<const char*>(bytes), sizeof(bytes));
}

void WriteNames(std::ofstream& stream, const std::vector<std::string>& names) {
  stream << '[';
  for (std::size_t index = 0; index < names.size(); ++index) {
    if (index != 0) stream << ',';
    stream << '"' << names[index] << '"';
  }
  stream << ']';
}

void WriteResult(const std::string& output, const RigResult& result, double duration, int fps) {
  const std::filesystem::path json_path(output);
  std::filesystem::path binary_path(json_path);
  binary_path.replace_extension(".bin");
  std::ofstream binary(binary_path, std::ios::out | std::ios::binary | std::ios::trunc);
  if (!binary) throw std::runtime_error("binary output could not be created");
  for (const auto& frame : result.weights) for (const float value : frame) WriteFloatLittleEndian(binary, value);
  if (!binary) throw std::runtime_error("binary output could not be written");
  binary.close();

  const auto jaw_open = ControlIndex(result.face_controls, "jawOpen");
  const auto left_in = ControlIndex(result.face_controls, "eyeLookInLeft");
  const auto left_out = ControlIndex(result.face_controls, "eyeLookOutLeft");
  const auto right_in = ControlIndex(result.face_controls, "eyeLookInRight");
  const auto right_out = ControlIndex(result.face_controls, "eyeLookOutRight");
  const auto left_up = ControlIndex(result.face_controls, "eyeLookUpLeft");
  const auto left_down = ControlIndex(result.face_controls, "eyeLookDownLeft");
  const auto right_up = ControlIndex(result.face_controls, "eyeLookUpRight");
  const auto right_down = ControlIndex(result.face_controls, "eyeLookDownRight");

  std::ofstream stream(json_path, std::ios::out | std::ios::trunc);
  if (!stream) throw std::runtime_error("metadata output could not be created");
  stream << std::fixed << std::setprecision(6);
  stream << "{\"version\":2,\"fps\":" << fps << ",\"duration\":" << duration
         << ",\"frame_count\":" << result.weights.size() << ",\"face_controls\":";
  WriteNames(stream, result.face_controls);
  stream << ",\"tongue_controls\":";
  WriteNames(stream, result.tongue_controls);
  stream << ",\"frames\":[";
  for (std::size_t index = 0; index < result.weights.size(); ++index) {
    if (index != 0) stream << ',';
    const auto& frame = result.weights[index];
    const float eye_x = std::clamp(
        ((Weight(frame, left_out) - Weight(frame, left_in)) +
         (Weight(frame, right_in) - Weight(frame, right_out))) * 0.5F, -1.0F, 1.0F);
    const float eye_y = std::clamp(
        ((Weight(frame, left_up) - Weight(frame, left_down)) +
         (Weight(frame, right_up) - Weight(frame, right_down))) * 0.5F, -1.0F, 1.0F);
    stream << "{\"t\":" << static_cast<double>(index) / fps
           << ",\"mouth_open\":" << Weight(frame, jaw_open)
           << ",\"eye_x\":" << eye_x << ",\"eye_y\":" << eye_y << '}';
  }
  stream << "]}";
  if (!stream) throw std::runtime_error("metadata output could not be written");
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Arguments arguments = ParseArguments(argc, argv);
    Check(nva2x::SetCudaDeviceIfNeeded(0), "select CUDA device");
    const std::vector<float> audio = ReadAudio(arguments.input);
    const RigResult result = Generate(audio, arguments.model, arguments.fps);
    WriteResult(arguments.output, result, static_cast<double>(audio.size()) / 16000.0, arguments.fps);
    return 0;
  } catch (const std::exception&) {
    std::cerr << "Audio2Face bridge failed" << std::endl;
    return 1;
  }
}
