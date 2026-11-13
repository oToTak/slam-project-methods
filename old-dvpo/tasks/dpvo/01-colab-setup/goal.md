# dpvo/01 — colab-setup

**Мета:** ноутбук `docker/dpvo/dpvo_colab.ipynb`, який на чистому Colab-рантаймі з GPU T4 ставить DPVO
і підключає Google Drive для даних і результатів.

**Готово, коли:**
- автор прогнав ноутбук згори донизу на новому рантаймі без ручних правок;
- `import dpvo` працює, `torch.cuda.is_available()` = True, ваги `dpvo.pth` на місці;
- DPVO відпрацював на кількох кадрах (будь-яке відео/папка) без помилок;
- у `run.md` записано: версії torch/CUDA/Python у Colab, час встановлення, коміт DPVO.

**Межі:** змінювати лише `docker/dpvo/`, цю папку завдання, `tasks/LOG.md`.
DPVO фіксувати на коміті `0ac95b6`. Без Pangolin/DPViewer, без DBoW2/DPRetrieval.
Секрети — лише через Colab Secrets (`HF_TOKEN`), у ноутбуці не друкувати.

**Читати:** `tasks/dpvo/README.md` («Що відомо»), README DPVO (у Colab), CONVENTIONS.md §8, §11.

**Питання автору:** немає (якщо збірка з дефолтним torch Colab не йде — запропонувати меню варіантів).
