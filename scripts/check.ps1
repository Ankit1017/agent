$ErrorActionPreference = "Stop"
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $python -m ruff format --check src tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m ruff check src tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m mypy src tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m pytest --cov=local_harness --cov-branch --cov-report=term-missing
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Test-Path "web\node_modules")) {
    Write-Error "Frontend dependencies are missing. Run npm ci --prefix web."
    exit 1
}
& npm run format:check --prefix web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& npm run lint --prefix web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& npm run typecheck --prefix web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& npm run test --prefix web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& npm run build --prefix web
exit $LASTEXITCODE
