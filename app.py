"""
app.py — Flask-сервис автоматизации релизного процесса IPC.
Запуск: python app.py  →  http://127.0.0.1:5000
"""

from flask import Flask, render_template, request, jsonify
from parser_pc import parse_powermart_xml
from generator import generate_plsql

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/parse', methods=['POST'])
def api_parse():
    """Принимает XML-файл, возвращает извлечённые метаданные."""
    if 'file' not in request.files:
        return jsonify({'error': 'Файл не загружен'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Файл не выбран'}), 400

    if not file.filename.lower().endswith('.xml'):
        return jsonify({'error': 'Ожидается файл с расширением .xml'}), 400

    try:
        content = file.read()
        data = parse_powermart_xml(content)
        return jsonify({'ok': True, 'data': data})
    except Exception as e:
        return jsonify({'error': f'Ошибка парсинга: {e}'}), 400


@app.route('/api/generate', methods=['POST'])
def api_generate():
    """Принимает параметры, возвращает готовый PL/SQL-скрипт."""
    try:
        params = request.get_json(force=True)
        sql = generate_plsql(params)
        return jsonify({'ok': True, 'sql': sql})
    except Exception as e:
        return jsonify({'error': f'Ошибка генерации: {e}'}), 400


if __name__ == '__main__':
    print("=" * 55)
    print("  IPC Release Tool  →  http://127.0.0.1:5000")
    print("=" * 55)
    app.run(debug=True, host='127.0.0.1', port=5000)
