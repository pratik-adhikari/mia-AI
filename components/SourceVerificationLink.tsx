export function SourceVerificationLink({
  threadId,
  evidenceId,
}: {
  threadId: string | null;
  evidenceId: string;
}) {
  if (!threadId) return null;
  return (
    <a
      href={`/workspace/source?thread=${encodeURIComponent(threadId)}&evidence=${encodeURIComponent(evidenceId)}`}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex rounded-full border border-signal/30 bg-signal/5 px-3 py-1.5 text-[11px] font-medium text-signal hover:bg-signal/10"
    >
      Inspect saved source ↗
    </a>
  );
}
