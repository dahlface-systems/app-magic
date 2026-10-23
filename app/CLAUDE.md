# App Magic (source)

This folder is App Magic's own code. Read `service/rules/workshop.md` first: it says how the app is put together, how to
make a change, how to put it in front of the person (`./magic reload` or `./magic restart`), and the lines not to cross.

Quick map: `ui/` what the person sees · `service/` what App Magic does (Python, standard library) · `shell/` the Mac app
(Swift) · `magic` the command line · `tests/` the tests. The person's data lives in `~/App Magic`, not here.
