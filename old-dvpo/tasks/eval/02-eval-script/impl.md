# eval/02 — impl

**Статус:** виконано.

**Зроблено**
- `scripts/dpvo/eval.sh <tum|ucu> <seq> [run]` (run за замовчуванням — найбільший `traj_run<N>.txt`; venv `~/venvs/slam`,
  matplotlib `Agg`). GT: `data/tum/<seq>` або `data/ucu_tum/<seq>`; результати: `results/dpvo/mono/<dataset>/<seq>/`.
- `evo_ape tum … -as --t_max_diff 0.02 -v --save_results ape_run<N>.zip`; друкує `Matched timestamps N/M`.
- TUM: додатково `evo_rpe … --delta 1 --delta_unit f` (`rpe_run<N>.zip`); УКУ — RPE не рахується.
- `evo_traj … --plot_mode xy --save_plot traj_xy.pdf` (GT у evo сірий за замовчуванням).
- Допоміжний `scripts/dpvo/update_meta.py`: оновлює `coverage` (2 знаки, за часом vs `rgb.txt`) і, якщо N < 0.9·M,
  дописує в `notes` «ATE matched N/M timestamps» (повторний запуск не дублює).
- `.gitignore` вже містить `*.zip` (перевірено `git check-ignore`) — не чіпав.
- Для тестів змінна `ROOT` підміняє корінь репо (у `results/` реальні файли не створювались).

**Перевірка** (синтетика у scratchpad, evo 1.37.1)
- TUM: траєкторія = 2×GT + шум σ=0.05/вісь, 80 % сиквенсу → N/M 1600/1600, ATE RMSE 0.043 м (очікувано ≈0.05·√3/2 ≈ 0.043),
  RPE RMSE 0.061, coverage 0.80.
- УКУ: 0.5×GT + шум σ=0.1/вісь, GT 1 Hz → N/M 100/100, ATE 0.335 м (очікувано ≈0.35), coverage 1.00, RPE не рахується.
- Розріджена траєкторія (0.3 с) → N/M 34/100 → запис у `notes` працює, повторний запуск ідемпотентний.

**Відхилення:** `--t_max_diff 0.02` і `-as` також передано в `evo_traj` для графіка (узгоджено з §7).
У evo «M» = min(кількість поз GT, траєкторії), тож для УКУ з покадровою траєкторією M = кількість поз GT.

**Питання автору:** немає.
