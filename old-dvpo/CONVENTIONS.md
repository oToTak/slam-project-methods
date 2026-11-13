# CONVENTIONS — спільні правила проєкту

Обов'язкові для всіх трьох. Зміни — тільки за згодою команди.

## 1. Загальне

- Кодування текстових файлів: **UTF-8**, переноси рядків **LF**.
  У репо `.gitattributes`: `* text=auto eol=lf`.
- Код, коментарі, назви файлів — англійською. Документація — українською або англійською.
- Десятковий роздільник — крапка. Одиниці: метри, секунди, радіани.

## 2. Структура репо

```
configs/<method>/                    # конфіги методу (UCU_mono.yaml тощо)
docker/<method>/                     # Dockerfile / build.sh
scripts/                             # спільні скрипти (export, eval, plot)
scripts/<method>/                    # run-скрипти методу
data/                                # НЕ в git; лише data/README.md
results/<method>/<mode>/<dataset>/<seq>/   # результати прогонів
results/summary.csv                  # зведена таблиця
docs/                                # слайди, картинки
```

Назви (рівно так):
- методи: `orbslam3`, `dpvo`, `dso`, `openvins`, `dpvslam`, `kimera`, `wheel_odom` (baseline)
- датасети: `tum`, `euroc`, `ucu`
- сиквенси: `seq01`…`seq13`, `fr1_xyz`, `MH_01`
- режими: `mono`, `mono_inertial`, `rgbd`, `rgbd_inertial`, `stereo_inertial`

## 3. Дані

Локально, поза git:
```
data/ucu/sequence-1/        # сирий .mcap з HF
data/ucu_tum/seq01/         # експорт у TUM
data/tum/fr1_xyz/
data/euroc/MH_01/
```

- Експорт УКУ → TUM **лише** офіційним скриптом
  (github.com/ucu-autonomous-ugv/ucu-slam-dataset, Python ≥ 3.12):
  `python scripts/export_tum.py data/ucu/sequence-1 data/ucu_tum/seq01`
- **Depth з УКУ v1 не використовуємо** (зламаний).
- Калібрування — **лише** з `calibration/calibration.yaml` репо датасету (Kalibr),
  не значення виробника з camera_info.
- Таймстемпи в даних УКУ вже зсунуті в час base (Δt камера–IMU = 14.9 мс врахований) —
  додатково нічого не зсуваємо.

## 4. Формат траєкторії (вихід кожного методу)

- Формат **TUM**, пробіли, без заголовка:
  `timestamp tx ty tz qx qy qz qw`
- `timestamp` — секунди Unix-часу, **9 знаків** після крапки.
- Поза камери (`camera_color`) у світовій системі методу. Кватерніон: **x y z w**.
- Поза для **кожного кадру**, а не лише keyframes. GT УКУ — 1 Hz, і з keyframes-траєкторією
  при `--t_max_diff 0.02` збігається лише ~10 % точок GT (ORB-SLAM3 mono seq01: 18 з 218),
  тож ATE рахується по випадковій вибірці. Для ORB-SLAM3 mono — `SaveTrajectoryEuRoC`
  (Fix 3 у `docker/orbslam3/setup_orbslam3.sh`).
- Файл: `results/<method>/<mode>/<dataset>/<seq>/traj_run<N>.txt` (напр. `results/orbslam3/mono/ucu/seq01/traj_run1.txt`) (N = 1, 2, 3…)
- Поруч `meta.yaml`:

```yaml
method: orbslam3
mode: mono
dataset: ucu
seq: seq01
run: 1
commit: <git sha нашого репо>
config: configs/orbslam3/UCU_mono.yaml
coverage: 1.00            # частка сиквенсу за часом, див. §5
tracking_lost: false      # повний збій
notes: ""
```
`meta.yaml` один на папку сиквенсу й описує **останній** прогін; попередні — в історії git
і в рядках `summary.csv`.

## 5. Оцінювання

- Інструмент: **evo==1.37.1** (не міняти до фіналу). Чому саме ця версія:
  - 1.38.0 вийшла 12.10.2026, вона надто свіжа;
  - усі версії від 1.32.0 потребують Python ≥ 3.10;
  - системний Python Ubuntu 20.04 — 3.8, на ньому `pip install evo` мовчки поставить стару 1.31.1, і результати в людей розійдуться.
- **Python-середовище для всіх скриптів (експорт + оцінка) — одне й те саме: Python 3.12 у venv**
  (3.12 потрібен і для `export_tum.py`). Системний Python для цього не використовувати.
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh     # один раз
  uv venv --python 3.12 ~/venvs/slam && source ~/venvs/slam/bin/activate
  uv pip install -r requirements.txt
  ```
  `requirements.txt` (корінь репо):
  ```
  evo==1.37.1
  rosbags==0.11.9
  tqdm==4.70.1
  pillow==13.0.0
  numpy==2.5.4
  ```
  Набір перевірено 20.10.2026 у чистому Ubuntu 20.04 + uv + CPython 3.12.
  Якщо `astral.sh` не відкривається: `sudo apt install -y python3-pip && python3 -m pip install --user uv`.
  Перевірка: `evo pkg --version` має показати 1.37.1.
- Ground truth:
  - УКУ: `groundtruth.txt` з експорту — **лише позиція** base_link, ~1 м, 1 Hz; орієнтацію ігноруємо
  - TUM: `groundtruth.txt`
  - EuRoC: `state_groundtruth_estimate0/data.csv` → конвертувати в TUM
- Вирівнювання:
  - моно (без метричного масштабу) → **Sim(3)**: `-as`
  - VIO / RGB-D (метричний масштаб) → **SE(3)**: `-a`
- Асоціація за часом: `--t_max_diff 0.02`
- **ATE** (головна метрика) — трансляційний RMSE, метри:
  ```
  evo_ape tum <gt> <traj> -as --t_max_diff 0.02 --save_results results/<...>/ape_run<N>.zip
  ```
  Перевірка: `evo_ape ... -v` друкує `Found N of max. M possible matching timestamps`.
  Для УКУ має бути N ≥ 0.9·M (з поправкою на coverage); інакше — див. §4 (покадрова траєкторія)
  і записати N/M у `meta.yaml → notes`.
- **RPE**
  - **УКУ — не рахуємо, поле `rpe_trans_m` порожнє.** GT без орієнтації (кватерніони одиничні),
    а `evo_rpe` виражає відносне переміщення в системі першої пози відрізка: для GT це ENU,
    для методу — система камери, тож похибка ≈ довжина відрізка навіть для ідеальної траєкторії
    (ORB-SLAM3 mono seq01: 25.7 і 14.5 м на відрізках 10 м).
  - TUM / EuRoC: `--delta 1 --delta_unit f`
- **Неповні прогони:** ATE по відтрекованій частині + обов'язково
  `coverage = (t_last − t_first траєкторії) / (t_last − t_first rgb.txt)` — за часом,
  бо деякі методи (ORB-SLAM3 mono) зберігають лише keyframes, і рахунок кадрів там не працює.
  Повний збій → `tracking_lost: true`, ATE = порожньо.
- **Кількість прогонів:** midterm — мінімум 1; фінал — 3, звітуємо медіану.
- Лаг-арм камера↔base (~5 см) ігноруємо — на порядок менший за похибку GNSS.

## 6. Таблиця результатів

`results/summary.csv` — кожен дописує свої рядки:

```csv
method,mode,dataset,seq,run,ate_rmse_m,rpe_trans_m,coverage,tracking_lost,commit
orbslam3,mono,tum,fr1_xyz,1,0.012,,1.00,false,abc1234
```

Порожнє поле = не рахувалось. Конфлікт у git — залишити обидва рядки.

## 7. Графіки

- Траєкторія поверх GT, вид зверху:
  `evo_traj tum <traj> --ref <gt> -as --plot_mode xy --save_plot results/<...>/traj_xy.pdf`
- seq09 (рельєф) — додатково `--plot_mode xz`.
- Однакові кольори на всіх слайдах: GT — сірий, метод — свій колір.

## 8. Відтворюваність

- `docker/<method>/build.sh` (або інструкція в README) — збирає середовище.
- `scripts/<method>/run.sh <dataset> <seq>` → `traj_run1.txt` + `meta.yaml`.
- `scripts/eval_all.sh` — проходить усі `results/*/*/*/*/traj_run*.txt` і перебудовує
  `summary.csv` (одна команда для ключової цифри).

## 9. Git

- Гілка на метод: `orbslam3`, `dpvo`, `openvins`; у `main` — через PR.
- Коміти малі й часті; повідомлення англійською, наказовий спосіб:
  `Add ORB-SLAM3 build script`.
- Не комітити: дані, `.zip` з evo, бінарники, `build/`.
- Код з особистих репо переносити зі збереженням історії
  (`git merge --allow-unrelated-histories` або `git subtree add`).

## 10. Доступи і ресурси (стан на 20.10.2026)

Оновлювати при кожній зміні. Токени, паролі й ключі сюди **не писати** — кожен має свої.

| Ресурс | Посилання | Доступ | Стан |
|---|---|---|---|
| Датасет УКУ (HF, gated) | huggingface.co/datasets/ucu-autonomous-ugv/ucu-slam-dataset-v1 | персональний: кожен логіниться і приймає умови сам; `hf auth login` своїм токеном | ✅ Ярема · ❓ Теодор · ❓ Олександр |
| Документація датасету (PDF) | папка `documentation/` на HF | разом з HF | ✅ прочитана |
| Репо пайплайну датасету (калібрування, `export_tum.py`) | github.com/ucu-autonomous-ugv/ucu-slam-dataset | публічний | ✅ |
| Репо пропоузалу (фінальний) | github.com/ototak/slam-algorithms-proposal | команда | ✅ пропоузал прийнято |
| Робочий репо з кодом (orbslam3) | github.com/gruverty/slam-orbslam3 | Ярема | ✅ створено 20.10.2026 |
| `husky-sim` (пофікшена збірка ORB-SLAM3) | організація ucu-autonomous-ugv | треба запросити в ментора | ❌ немає |
| TUM RGB-D | cvg.cit.tum.de/data/datasets/rgbd-dataset | публічний | ✅ |
| EuRoC MAV | projects.asl.ethz.ch/datasets/euroc-mav | публічний | ✅ |
| ORB-SLAM3 | github.com/UZ-SLAMLab/ORB_SLAM3 | публічний | ✅ |
| Ментор | Олександр Косован | — | питання надіслати 20.10 (див. PLAN.md §8) |

Залізо:

| Хто | ОС | RAM | Диск | GPU |
|---|---|---|---|---|
| Ярема | Windows 11 → WSL2 Ubuntu 20.04 | 12 GB | 270 GB | ❓ |
| Теодор | ❓ | ❓ | ❓ | ❓ (критично для DPVO) |
| Олександр | ❓ | ❓ | ❓ | ❓ |

## 11. Контекст для AI-асистентів

Кожен, хто працює з AI-асистентом (Claude, ChatGPT тощо), дає йому на старті
**цей файл + `PLAN.md`**. Асистент має знати й дотримуватись:

**Ключові факти (не перевідкривати):**
- Тема: бенчмарк SLAM на UCU SLAM Dataset v1; пропоузал прийнято; midterm пн 26.10.2026 (здача PDF + GitHub до кінця 25.10).
- Сенсори: RealSense D435 RGB 640×480@30, IMU Phidgets 1042 @125 Hz, wheel odom, GNSS F9P без RTK @1 Hz.
  Стерео і LiDAR немає.
- **Depth у v1 зламаний** (у seq-1: 386 depth-кадрів на 6532 RGB) — не використовувати.
- GT — лише позиція base_link в ENU, точність ~1 м, без орієнтації.
- На HF у `sequence-N/` лише `.mcap` + `metadata.yaml`; TUM-файли генерувати `export_tum.py`.
- Калібрування: `calibration/calibration.yaml` з GitHub-репо датасету.
  IMU frame = base_link (T_base_imu = I).
- ⚠️ **Екстрінсік камера↔IMU: у датасеті ймовірна інверсія.** `T_base_color` у `calibration.yaml`
  (і в `/tf_static` як base→color) чисельно = Kalibr `T_cam_imu` (IMU→камера, `T_C←I`),
  а не `T_B←C`. Для методів, яким потрібна поза камери в системі IMU (ORB-SLAM3 `IMU.T_b_c1`,
  OpenVINS `T_imu_cam`), брати **Kalibr `T_ic` (cam0 → imu0)** з `calibration/raw/results-imucam.txt`
  = обернена до `T_base_color`:
  ```
  [-0.9993405708,  0.0279487231,  0.0231795686,  0.0496746712]
  [ 0.0232085203,  0.0007119016,  0.9997303925,  0.0036884442]
  [ 0.0279246864,  0.9996091046, -0.0013600806, -0.0504146570]
  [ 0,             0,             0,             1           ]
  ```
  (Поворот симетричний, тому помилку видно лише за трансляцією.) Повідомлено/повідомити ментору.
- Таймстемпи вже в часі base — не зсувати.
- Відгук ментора: sanity check на TUM RGB-D перед даними УКУ; ORB-SLAM3 складно збирати
  на нових системах (фікси в `husky-sim`); IMU у TUM-форматі не підтримується `rgbd_tum` —
  потрібен свій лоадер.

**Правила для асистента:**
- Не змінювати формати, назви і метрики з цього файлу; пропозиції змін — тільки текстом для команди.
- Не комітити й не пушити від імені людини без явного прохання.
- Не просити і не зберігати токени HF/GitHub.
- Асистент не має доступу до gated HF і до приватних чатів інших учасників —
  потрібні дані (README, metadata.yaml, логи помилок) вставляти йому текстом.
- Використання AI фіксується в секції «AI usage» README (вимога курсу).

## 12. Власники

| Блок | Власник |
|---|---|
| orbslam3 | Ярема |
| dpvo / dso | Теодор |
| openvins, wheel_odom baseline, `data/README.md`, README, `scripts/eval_all.sh` | Олександр |
| `CONVENTIONS.md`, `summary.csv`, слайди | усі |
