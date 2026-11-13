# dpvo/03 — sanity-tum

**Мета:** переконатися, що збірка правильна: DPVO на TUM `fr1_xyz` дає ATE, близький до опублікованого.

**Готово, коли:**
- автор запустив у Colab `scripts/dpvo/run.sh tum fr1_xyz` і поклав результат у `results/dpvo/mono/tum/fr1_xyz/`;
- ATE пораховано за CONVENTIONS §5 (`-as`, `--t_max_diff 0.02`), RPE з `--delta 1 --delta_unit f`;
- `run.md`: команда, час прогону, ATE, N/M відповідних таймстемпів, порівняння зі статтею
  (таблиця TUM-RGBD у DPV-SLAM, ECCV 2024 — порядок 1–2 см); розбіжність > 2× — пояснити або «блокер».

**Межі:** змінювати лише `results/dpvo/mono/tum/fr1_xyz/`, цю папку завдання, `tasks/LOG.md`.

**Читати:** `tasks/dpvo/README.md`, `tasks/eval/README.md`, CONVENTIONS.md §5, §7.

**Питання автору:** немає.
