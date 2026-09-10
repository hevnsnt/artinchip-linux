# Instructions for the Agent: tinyscreen + the Todo Display

You are managing a ZHAOCAILIN 11.3" 1920x440 USB bar display connected to Bill's
Mac. It runs `tinyscreen`, a driver that streams frames to the display. Your job
is to (1) keep the display running the right mode, and (2) keep the todo list on
it accurate and current.

## 1. How to control the display

`tinyscreen` is installed on the Mac at `~/.tinyscreen/`, with a `tinyscreen`
command in PATH. One instance runs at a time; starting a new mode stops the old
one automatically.

```
tinyscreen --todo                     # the todo dashboard (see below)
tinyscreen --sysmon                   # system monitor
tinyscreen --clock                    # clock + weather
tinyscreen --url https://example.com  # website in headless Chrome
tinyscreen --image /path/to/img.jpg   # static image
tinyscreen --video clip.mp4           # video playback
tinyscreen --show todo sysmon clock --delay 30   # rotate modes
tinyscreen --status                   # is it running? which mode?
tinyscreen --off                      # stop and blank the display
```

Run with `--fg` when launching from a script or launchd so it stays in the
foreground. Without `--fg` it daemonizes and logs to `/tmp/tinyscreen.log`.

## 2. The todo display (--todo)

`tinyscreen --todo` renders a dashboard with:

- **TODAY** (left): overdue tasks in red at the top, then today's open tasks
  sorted by time, then completed tasks dimmed with a checkmark.
- **Next 3 days** (right): three columns for tomorrow, +2, and +3 days.

The mode re-reads its data file on every frame, so any change you make appears
on the display within about a second. You never need to restart tinyscreen after
editing the file.

## 3. The todo data file

Location: `~/.tinyscreen/todo.json` (override with env var `TINYSCREEN_TODO`).

Format:

```json
{
  "tasks": [
    {"title": "Pay estimated taxes", "due": "2026-09-10", "time": "09:00",
     "done": false, "tags": ["finance"]},
    {"title": "Call Mike", "due": "2026-09-11", "done": false}
  ]
}
```

Fields:

- `title` (required): the task text. Keep it short, under ~40 chars, so it fits
  on the display.
- `due` (required): `YYYY-MM-DD`. Tasks due before today and not done render as
  OVERDUE in red. Tasks due today show under TODAY. Tasks due in the next 3 days
  show in the upcoming columns. Anything further out is not shown, so only keep
  tasks that are due today or within 3 days.
- `time` (optional): `HH:MM` (24-hour). Used to sort today's tasks.
- `done` (optional, default false): completed tasks show dimmed under TODAY.
- `tags` (optional): list of strings, shown as `#tag`.

## 4. How to update the todo list

Two ways. Prefer the CLI for single edits; write JSON directly for bulk changes.

### Via the CLI

```
python3 ~/.tinyscreen/todo-cli.py add "Book dentist" --due 2026-09-11 --time 15:00 --tag health
python3 ~/.tinyscreen/todo-cli.py list
python3 ~/.tinyscreen/todo-cli.py done 0 2      # mark tasks 0 and 2 done
python3 ~/.tinyscreen/todo-cli.py undone 0      # mark task 0 not done
python3 ~/.tinyscreen/todo-cli.py rm 3          # delete task 3
```

`list` prints numbered rows. Use those numbers for `done`, `undone`, and `rm`.

### By writing the file directly

Read `~/.tinyscreen/todo.json`, edit the `tasks` array, write it back. Always
write valid JSON and always keep the top-level `{"tasks": [...]}` shape.

## 5. Keeping it "always updated" (your standing rules)

1. **Add tasks the moment Bill mentions them.** If he says "remind me to X" or
   "I need to do Y", add it with a `due` date. If he gives no date, default to
   today.
2. **Mark tasks done when he confirms completion.** If Bill says something is
   done, set `done: true` immediately. Do not re-ask.
3. **Prune stale items.** Remove or mark-done tasks that are finished, and drop
   tasks whose `due` date is more than 3 days in the future (the display only
   shows today + 3 days, so anything further out is invisible clutter).
4. **Keep it short.** The display fits roughly 6 to 8 tasks per day. If a day
   would overflow, merge related items or keep only the highest-priority ones.
5. **Fix dates as they pass.** If a task is still open after its due date, it
   shows as OVERDUE automatically. Either mark it done, or move its `due` date
   forward to when Bill will actually do it.
6. **Verify after writing.** After any edit, run `todo-cli.py list` and confirm
   the file is valid JSON and the tasks are what you intended. If the display
   is running `--todo`, the change appears within a second.

## 6. Daily maintenance (recommended cron)

Schedule one job per day, early morning, that:

1. Reads `~/.tinyscreen/todo.json`.
2. Deletes tasks that are `done` and whose `due` date is older than today.
3. Leaves everything else untouched.

This keeps the file from accumulating completed history. Do not move or
reschedule tasks automatically; only prune completed old ones.

## 7. Troubleshooting

- **Display shows "No todo list yet"**: the file is missing. Create
  `~/.tinyscreen/todo.json` with `{"tasks": []}` or add a task.
- **Display shows "Waiting for display"**: the USB display is not detected.
  Unplug and replug the cable.
- **Edits not appearing**: confirm tinyscreen is in `--todo` mode
  (`tinyscreen --status`) and that the JSON is valid.
- **Logs**: `tail -f /tmp/tinyscreen.log`.
