# dpvo/01 — impl

**Статус:** виконано (ноутбук написаний; автор ще має прогнати його в Colab — тоді `run.md` і закриття завдання).

## Що зроблено
- `docker/dpvo/dpvo_colab.ipynb` (nbformat 4, зібраний скриптом): A — GPU/версії, Drive, клон нашого репо через `GITHUB_TOKEN`, DPVO `0ac95b6` + eigen-3.4.0, залежності + `pip install --no-build-isolation .` (`TORCH_CUDA_ARCH_LIST=7.5`, час друкується), лише `models.zip`, перевірка `import dpvo`/CUDA/`dpvo.pth`, smoke test (TUM fr1_xyz, 150 кадрів, через наш `run_dpvo.py`, вихід у `/content/smoke_results`);
  B — окрема опційна клітинка torch 2.3.1/cu121 + numpy 1.26.4; C — TUM `run.sh tum fr1_xyz` (dpvo/03); D — УКУ: `snapshot_download` sequence-1, офіційний `export_tum.py` у uv-venv Python 3.12, tar експорту на Drive, `run.sh ucu seq01` (dpvo/04); E — zip результатів + копія в Drive + завантаження.
- `docker/dpvo/README.md` — коротка інструкція.
- Для smoke test додано `--max-frames` у `scripts/dpvo/run_dpvo.py` (dpvo/02).

## Як перевірено
Лише локально: ноутбук — валідний JSON nbformat 4, кожна python-клітинка компілюється, `%%bash` скрізь першим рядком, токенів у файлі немає. Сама збірка в Colab **не запускалась**; нічого з DPVO API не гадалось — залежності взято з `environment.yml` коміту `0ac95b6` (потрібен `torch_scatter`, `pypose`, `kornia`, `einops`, `yacs` тощо).

## Рішення
- Секрети: **`GITHUB_TOKEN`** (read-only PAT на цей репо) і `HF_TOKEN`. Токен у URL лише на час клону, потім `remote set-url` без токена; виводи фільтруються.
- Результати назад: zip + Drive, без git push з Colab (не потрібен токен із записом).
- 6.5 GB mcap — на локальний диск Colab, не на Drive; на Drive лише tar експорту. Після експорту mcap видаляється.
- Ноутбук спершу ставить дефолтний torch Colab (може не збудуватись — це відкрите питання напрямку); запас у розділі B.

## Що має зробити автор
1. Запушити репо (із `scripts/dpvo/`, `configs/dpvo/`) на GitHub; створити Secrets `GITHUB_TOKEN`, `HF_TOKEN`.
2. Прогнати розділ A на новому T4-рантаймі; записати у `run.md`: Python/torch/CUDA (друкує A7), час встановлення (A5), коміт DPVO.
3. Якщо A5/A7 падає — розділ B; якщо й він ні — прислати помилку.

## Невизначеності
- URL Dropbox `models.zip` і `data.pyg.org` wheel для актуального torch Colab не перевірялись.
- Python ≥ 3.12 для експорту забезпечує uv-venv, не системний Python Colab.

## Питання автору
Чи ок назви секретів `GITHUB_TOKEN` / `HF_TOKEN`? Інакше змінити в клітинках A3 і D2.
