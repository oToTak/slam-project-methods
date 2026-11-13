# dpvo/04 — ucu-seq01

**Мета:** перший прогін DPVO на даних УКУ: `sequence-1` → `results/dpvo/mono/ucu/seq01/`.

**Готово, коли:**
- у Colab: `hf download … --include "sequence-1/*"` → `export_tum.py data/ucu/sequence-1 data/ucu_tum/seq01`
  (офіційний скрипт, Python ≥ 3.12, CONVENTIONS §3); експорт збережено на Drive, щоб не повторювати;
- `scripts/dpvo/run.sh ucu seq01` дав `traj_run1.txt` + `meta.yaml`;
- ATE (`-as`), `coverage`, N/M таймстемпів (має бути N ≥ 0.9·M з поправкою на coverage), графік `traj_xy.pdf`;
  RPE для УКУ **не рахувати** (§5);
- `run.md`: час завантаження/експорту/прогону, цифри, де і чому губився трекінг (якщо губився).
  Повний збій — `tracking_lost: true`, це теж результат.

**Межі:** змінювати лише `results/dpvo/mono/ucu/seq01/`, цю папку завдання, `tasks/LOG.md`
(+ клітинки ноутбука в `docker/dpvo/` для завантаження/експорту, якщо їх там ще немає).
Depth не використовувати. Таймстемпи не зсувати.

**Читати:** `tasks/dpvo/README.md`, CONVENTIONS.md §3–§5, §7.

**Питання автору:** немає.
