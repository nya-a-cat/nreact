# Run storage and recovery

The local workbench stores runs under `.nreact/runs/`, beside its configuration
file. Open **Run history** to inspect a recorded run, or use **File → Export
selected run** to save its contents under `.nreact/exports/`.

## Files

Each run has an initial `<id>.json` manifest and an append-only `<id>.jsonl`
execution trace. The manifest is written before agent construction. A storage
failure at this stage prevents the run from starting.

The trace records the start, model generations, events and final result. Each
complete entry ends with a newline and is flushed from the Python writer. New
event entries include elapsed seconds; older traces remain readable, with
unavailable event timings carried forward from the last known value.

At completion, the manifest is replaced by the full run record. Replacement uses
a temporary file in the same directory, flushes and synchronizes its contents,
then atomically replaces the previous JSON file. Failed serialization or writes
leave the previous manifest intact. Record writes and reads are limited to
24,000,000 bytes. Recovery reads at most 72,000,000 trace bytes, with a
24,000,000-byte limit per entry.

Completed records are released from server memory. The history list retains the
most recent fifty summaries while scanning files; full traces are loaded one at
a time. Invalid record identifiers, malformed JSON and invalid record metadata
are skipped in the list. Opening a malformed record directly reports an error.

## Interrupted execution

When a server reads a manifest with a live status and has no matching live worker,
it reconstructs recorded events from the complete, valid prefix of the JSONL
trace. A truncated final line or corrupt entry stops recovery at the previous
readable entry. An incomplete run is shown as `interrupted`.

A valid final result in the trace restores the completed status, answer and usage,
even when the final JSON replacement failed. Recovery only reads local files. It
never constructs a model, resets an environment or executes a tool. It leaves the
original manifest and trace unchanged.

Use one workbench server per configuration directory. A second server cannot
control the first server's worker and treats its unfinished manifests as
interrupted. To execute another attempt, copy the recorded task to the working
copy and explicitly start a new run. Tool side effects from an earlier attempt
may already exist.

If the final record cannot be saved, the current server retains the full record
for export. Export it before closing the server. After fifty unsaved records,
further starts are rejected so additional results cannot accumulate indefinitely;
export the records, repair storage, and restart the server.

## Cancellation and errors

Stop requests cooperative cancellation. An in-flight model or tool call must
return before cancellation takes effect. Completed tool feedback remains in the
trace. A stop requested during the last tool call produces `cancelled`, including
when that call completes the environment or exhausts the turn budget.

An already-cancelled run skips environment reset and generation. A failure in
`Environment.reset()` produces `environment_error`, with zero model calls and a
final trace result. Its error message contains the exception type; arbitrary
exception text is kept out of the record.

## Privacy and durability

Configuration snapshots omit saved API keys. Tasks, model output and tool
contents remain in the trace as supplied. Treat run records and exports as
potentially sensitive. New record and trace files request owner-only read/write
permissions on POSIX; Windows access follows the containing directory's ACLs.

Flushing each JSONL line supports recovery after a process interruption. Filesystem
and hardware behavior still affect recovery after power loss. Per-entry trace
writes do not call `fsync`, and the atomic manifest replacement does not synchronize
the parent directory. These files are local execution records; keep independent
backups for data requiring stronger durability.
