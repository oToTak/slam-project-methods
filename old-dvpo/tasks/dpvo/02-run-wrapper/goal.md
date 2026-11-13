# dpvo/02 — run-wrapper

**Мета:** одна команда `scripts/dpvo/run.sh <dataset> <seq>` (CONVENTIONS.md §8) у Colab
дає `results/dpvo/mono/<dataset>/<seq>/traj_run<N>.txt` + `meta.yaml`.

**Готово, коли:**
- `scripts/dpvo/run_dpvo.py` читає кадри й **таймстемпи з `rgb.txt`** (не номери кадрів), `stride=1`;
- траєкторія — TUM, без заголовка, `timestamp` з 9 знаками, поза для кожного кадру (CONVENTIONS §4);
- `meta.yaml` за шаблоном §4: `commit`, `config`, `coverage` (за часом, §5), `tracking_lost`, `notes`;
- `configs/dpvo/calib_ucu.txt`, `configs/dpvo/calib_tum_fr1.txt` (значення — у README напрямку);
  для TUM — обрізка як у `evaluate_tum.py`;
- `N` у `traj_run<N>` — наступний вільний номер, старі прогони не перезаписуються;
- запис файлів і `coverage` перевірені локально на Mac без GPU (синтетичні пози), `impl.md` описує як.

**Межі:** змінювати лише `scripts/dpvo/`, `configs/dpvo/`, цю папку завдання, `tasks/LOG.md`.
Код DPVO не правити — лише імпортувати. Шляхи даних — за CONVENTIONS §3 (`data/tum/fr1_xyz/`, `data/ucu_tum/seq01/`).

**Читати:** `tasks/dpvo/README.md`, CONVENTIONS.md §2–§5, §8.

**Питання автору:** немає.
