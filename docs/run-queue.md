# Run queue and live updates

## Queue tasks

Save the model and tool configuration, enter a task, then choose **Run → Queue
task**. An idle, resumed queue starts immediately. While a task is running,
select **Working copy** to prepare another task with the same or a different
saved configuration. Queued tasks execute in submission order with one active
worker per workbench server. At most 32 tasks may wait in the queue.

**Run → Pause queue** holds pending tasks; the active task continues. **Resume
queue** allows the next pending task to begin. The status bar displays the pending
count and whether the queue is paused. Open **Run history** to inspect queued,
active and completed tasks. A selected queued task can be removed with **Stop**;
its cancelled record remains in history and no model or tool is constructed.

**Stop** on the active task requests cooperative cancellation and pauses the
queue. In-flight model or tool calls finish before cancellation takes effect.
Resume the queue explicitly after checking the stopped task. A model error,
environment initialization failure, worker failure or final-record storage error
also pauses the queue. Successful tasks continue to the next queued task.

**Run** and **Step** start immediate tasks when there is no active or pending work.
They never bypass an existing queue. Queued tasks run continuously; use the normal
Pause/Step controls once a queued task becomes active to inspect turn boundaries.

## Snapshots and restart behavior

Each submission copies the saved model and tool settings and task. Later edits
to the working configuration leave those execution inputs unchanged. Literal API
keys remain in private memory and are omitted from recorded configuration
snapshots. Environment variables, OAuth credentials and workspace files are read
when execution uses them; their live contents are not frozen by queue submission.

A manifest is created before admission. Restarted servers expose unfinished
queued manifests as `interrupted` and never automatically start them. Inspect the
history and explicitly submit a new attempt after restart. See
[Run storage and recovery](run-recovery.md) for durability, size limits and the
single-server-per-configuration-directory requirement.

## Connection handling

The browser checks lightweight lifecycle state and fetches only unseen event
batches for the selected live run. Large batches are caught up in bounded chunks;
event-list previews are limited to 400 characters while the inspector and export
retain the full recorded text. Opening a saved run and exporting it still fetch
its complete record. New jobs from another browser window appear without
replacing local task or configuration drafts.

Read failures show a connection message and retry with increasing delays up to
eight seconds. Hidden browser tabs suspend polling, and becoming visible triggers
a fresh check. Requests time out after fifteen seconds. Mutating requests such as
Run, Queue, Save and Stop are sent once. If a response is lost, the interface warns
that the operation may have been applied; inspect history or reload saved data
before repeating it. Restarting the server changes its session token, so reload
the page to establish a new session.
