import os
from pathlib import Path

# Настройки
OUTPUT_FILENAME = "full_project_backup.txt"
ROOT_DIR = "."

# Папки, которые нужно игнорировать (виртуальное окружение, git, кэш)
IGNORE_DIRS = {
    '.git', '__pycache__', 'venv', '.venv', 'env', 'docs', 'data', 'nginx',
    'node_modules', 'dist', 'build', '.idea', '.vscode', '.github', 'reports'
}

# Расширения файлов, которые нужно включить
ALLOWED_EXTENSIONS = {
    '.py', '.html', '.css', '.js'
}

def create_snapshot():
    print(f"Начинаю создание снапшота проекта в {os.path.abspath(ROOT_DIR)}...")
    
    output_path = Path(OUTPUT_FILENAME)
    
    with open(output_path, 'w', encoding='utf-8') as out_file:
        out_file.write(f"# Полный снапшот проекта\n")
        out_file.write(f"# Сгенерировано автоматически\n\n")

        # os.walk проходит по всем папкам рекурсивно
        for root, dirs, files in os.walk(ROOT_DIR):
            # Исключаем ненужные папки из обхода
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            
            # Сортируем файлы для красивого порядка
            files.sort()

            for file in files:
                file_path = Path(file)
                
                # Проверяем расширение
                if file_path.suffix.lower() in ALLOWED_EXTENSIONS:
                    full_path = os.path.join(root, file)
                    
                    # Записываем заголовок в стиле "как ты кидал"
                    # Преобразуем путь в читаемый вид (убираем './' в начале)
                    relative_folder = root.replace('.\\', '').replace('./', '')
                    if relative_folder == '.': relative_folder = ""
                    
                    out_file.write(f"\n{'='*60}\n")
                    if relative_folder:
                        out_file.write(f"Папка {relative_folder}:\n")
                    out_file.write(f"{file}:\n")
                    out_file.write(f"{'='*60}\n")
                    
                    # Читаем и пишем содержимое файла
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            out_file.write(content)
                    except Exception as e:
                        out_file.write(f"Ошибка чтения файла: {e}")
                    
                    out_file.write(f"\n\n")

    print(f"Готово! Весь код сохранен в файл: {output_path.absolute()}")

if __name__ == "__main__":
    create_snapshot()