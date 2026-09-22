import { WorkspaceSidebar } from "@/components/WorkspaceSidebar";

export default function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen overflow-hidden bg-mist">
      <WorkspaceSidebar />
      <main className="ml-[240px] flex min-h-0 flex-1 flex-col overflow-hidden">
        {children}
      </main>
    </div>
  );
}
