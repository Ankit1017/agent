import { AppHeader, EmptyState, StatusRegion } from "./ui";

/** Explains why Studio is not part of the protected main build. */
export default function StudioUnavailablePage() {
  return (
    <div className="module-notice-page">
      <AppHeader
        current="studio"
        title="Comedy Video Studio"
        status={<StatusRegion tone="warning">Optional module</StatusRegion>}
      />
      <main id="main-content">
        <EmptyState title="Studio is isolated from the main harness">
          Switch to the Studio branch and run the normal service launcher to use
          authorized media downloads and local Blender rendering.
        </EmptyState>
      </main>
    </div>
  );
}
