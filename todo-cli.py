#!/usr/bin/env python3
"""
todo-cli.py - edit the tinyscreen todo list from the command line.

Reads/writes ~/.tinyscreen/todo.json (override with TINYSCREEN_TODO).

Usage:
  todo-cli.py add "Title" [--due YYYY-MM-DD] [--time HH:MM] [--tag TAG]...
  todo-cli.py list
  todo-cli.py done N [N...]      mark task(s) done by number
  todo-cli.py undone N [N...]    mark task(s) not done
  todo-cli.py rm N [N...]        delete task(s) by number
"""

import argparse
import json
import os
import sys
from datetime import date

TODOFILE = os.environ.get('TINYSCREEN_TODO', os.path.expanduser('~/.tinyscreen/todo.json'))


def _load():
    try:
        with open(TODOFILE) as f:
            data = json.load(f)
        return data.get('tasks', []) if isinstance(data, dict) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"ERROR: could not read {TODOFILE}: {e}", file=sys.stderr)
        sys.exit(1)


def _save(tasks):
    os.makedirs(os.path.dirname(TODOFILE), exist_ok=True)
    tmp = TODOFILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({'tasks': tasks}, f, indent=2)
    os.replace(tmp, TODOFILE)


def _print_list(tasks):
    if not tasks:
        print("No tasks.")
        return
    today = date.today()
    for i, t in enumerate(tasks):
        due = t.get('due', '')
        tme = t.get('time', '')
        done = 'x' if t.get('done') else ' '
        tags = ' '.join('#' + str(x) for x in (t.get('tags') or []))
        when = f"{due} {tme}".strip()
        flag = ''
        if due and not t.get('done'):
            try:
                d = date.fromisoformat(due)
                if d < today:
                    flag = ' OVERDUE'
            except Exception:
                pass
        print(f"[{done}] {i:3d}  {when:18s}  {t.get('title','')}  {tags}{flag}")


def _parse_indices(args, tasks):
    idxs = []
    for s in args:
        try:
            i = int(s)
        except ValueError:
            print(f"ERROR: '{s}' is not a number", file=sys.stderr)
            sys.exit(1)
        if i < 0 or i >= len(tasks):
            print(f"ERROR: index {i} out of range (0-{len(tasks)-1})", file=sys.stderr)
            sys.exit(1)
        idxs.append(i)
    return idxs


def main():
    p = argparse.ArgumentParser(description='Edit the tinyscreen todo list')
    sub = p.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('add', help='add a task')
    a.add_argument('title')
    a.add_argument('--due', default=date.today().isoformat(), help='YYYY-MM-DD (default today)')
    a.add_argument('--time', default='', help='HH:MM')
    a.add_argument('--tag', action='append', default=[], help='repeatable tag')

    sub.add_parser('list', help='list tasks')

    for name in ('done', 'undone', 'rm'):
        s = sub.add_parser(name, help=f'{name} task(s) by number')
        s.add_argument('indices', nargs='+', help='task numbers from list')

    args = p.parse_args()
    tasks = _load()

    if args.cmd == 'add':
        tasks.append({
            'title': args.title,
            'due': args.due,
            'time': args.time,
            'done': False,
            'tags': args.tag,
        })
        _save(tasks)
        print(f"Added: {args.title} ({args.due} {args.time})")

    elif args.cmd == 'list':
        _print_list(tasks)

    elif args.cmd in ('done', 'undone', 'rm'):
        idxs = _parse_indices(args.indices, tasks)
        for i in sorted(idxs, reverse=True):
            if args.cmd == 'done':
                tasks[i]['done'] = True
            elif args.cmd == 'undone':
                tasks[i]['done'] = False
            else:  # rm
                del tasks[i]
        _save(tasks)
        print(f"{args.cmd}: {len(idxs)} task(s) updated. Total now {len(tasks)}.")


if __name__ == '__main__':
    main()
