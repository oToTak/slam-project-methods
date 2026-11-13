# docker/dpvo — DPVO у Google Colab

Збірка й прогін DPVO (mono, коміт `0ac95b6`) — ноутбук `dpvo_colab.ipynb`. Локально (Mac, без CUDA) DPVO не запускається.

## Як користуватися
1. Запушити репо на GitHub (ноутбук клонує `oToTak/ai_project_v.piter`, гілка `REPO_BRANCH` у клітинці A3, типово `main`).
2. Colab -> відкрити `dpvo_colab.ipynb` (Upload або з GitHub), Runtime -> **T4 GPU**.
3. Colab Secrets (ключ ліворуч), увімкнути *Notebook access*:
   - `GITHUB_TOKEN` — fine-grained PAT, лише цей репо, Contents: Read-only;
   - `HF_TOKEN` — для gated-датасету УКУ (розділ D).
4. Виконати розділ **A** (збірка + smoke test на 150 кадрах TUM; ~10-20 хв, час друкується).
   Якщо A5/A7 впали з дефолтним torch Colab — розділ **B** (torch 2.3.1/cu121, перезапуск рантайму), потім A знову.
5. Розділ **C** — `scripts/dpvo/run.sh tum fr1_xyz`, розділ **D** — експорт УКУ (`export_tum.py`) і `scripts/dpvo/run.sh ucu seq01`.
6. Розділ **E** — zip з `results/dpvo/` (копія в Drive `slam_dpvo/` + завантаження в браузер). Розпакувати в корінь репо на Mac.

## Де що лежить у Colab
- `/content/DPVO` — DPVO + `dpvo.pth`; `/content/ai_project_v.piter` — наш репо (`data/`, `results/`).
- Drive `MyDrive/slam_dpvo/` — `ucu_tum_seq01.tar` (експорт seq01, щоб не повторювати 6.5 GB) і zip результатів.

Скрипт прогону: `scripts/dpvo/run.sh <tum|ucu> <seq>` (змінна `DPVO_DIR`, типово `/content/DPVO`).
