import { EmptyState, PageHeader } from "../components/ui";

export function SettingsPage() {
  return (
    <>
      <PageHeader eyebrow="Preferences" title="Settings" description="Repository rules and dashboard preferences." />
      <section className="surface-panel settings-panel">
        <EmptyState title="Repository settings live with the code" description="Finding exclusions and severity controls are configured in .docdrift.yml. Dashboard preferences are not available yet." />
      </section>
    </>
  );
}
