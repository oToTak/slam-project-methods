# eval/02 — eval-script

**Мета:** `scripts/dpvo/eval.sh <dataset> <seq> [run]` — ATE, (RPE для TUM), coverage, графік для одного прогону.

**Готово, коли:**
- для `tum` бере GT `data/tum/<seq>/groundtruth.txt`, для `ucu` — `data/ucu_tum/<seq>/groundtruth.txt`;
- `evo_ape tum <gt> <traj> -as --t_max_diff 0.02 -v --save_results …/ape_run<N>.zip`; друкує N/M;
- для TUM — `evo_rpe … --delta 1 --delta_unit f`; для УКУ RPE не рахує;
- оновлює `coverage` у `meta.yaml`; N/M пише в `notes`, якщо N < 0.9·M;
- `evo_traj … --plot_mode xy --save_plot …/traj_xy.pdf` (GT — сірий);
- перевірено на синтетичній траєкторії (GT зі шумом) — ATE очікуваного порядку;
- `.gitignore` не пускає `*.zip` у git.

**Межі:** змінювати лише `scripts/dpvo/eval.sh` (+ допоміжний `.py` поруч), цю папку завдання, `tasks/LOG.md`.

**Читати:** `tasks/eval/README.md`, CONVENTIONS.md §4–§7.

**Питання автору:** немає.
