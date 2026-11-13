# Напрямок eval

**Мета:** на Mac однією командою отримати для свого прогону ATE, coverage і графік за CONVENTIONS.md §5, §7.

**Готово, коли:**
- `~/venvs/slam` (Python 3.12, uv) з `requirements.txt` (§5), `evo pkg --version` = 1.37.1;
- `scripts/dpvo/eval.sh <dataset> <seq> [run]` рахує ATE (`-as --t_max_diff 0.02`), для TUM — RPE,
  друкує N/M таймстемпів, оновлює `coverage` у `meta.yaml`, зберігає `traj_xy.pdf`;
  `ape_run<N>.zip` — поруч, але не в git.

**Межі:** лише власні результати DPVO. `scripts/eval_all.sh` і `summary.csv` загалом — зона Олександра,
тут не писати; формат виходу — сумісний з ними.

## Що відомо

- GT: TUM — `groundtruth.txt`; УКУ — `groundtruth.txt` з експорту (лише позиція, ~1 м, 1 Hz).
- Моно → Sim(3) `-as`. Для УКУ RPE не рахувати (§5).
- `coverage = (t_last − t_first траєкторії) / (t_last − t_first rgb.txt)`.

## Відкрите

- ~~Колеса `numpy==2.5.4` для macOS 12 Intel / Python 3.12~~ — є, venv ставиться за ~17 с (eval/01).
- evo «M» у `Found N of max. M` = min(поз GT, поз траєкторії) → для покадрової траєкторії УКУ M = кількість поз GT.

## Завдання

| # | Завдання | Тип | Стан |
|---|---|---|---|
| 01 | [venv](01-venv/goal.md) — середовище evo на Mac | Реалізація | закрито |
| 02 | [eval-script](02-eval-script/goal.md) — `scripts/dpvo/eval.sh` | Реалізація | закрито |
