import uuid
import time
import random
from datetime import datetime
from threading import Lock
from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'секрет!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')


class GameManager:
    def __init__(self):
        self.games = {}
        self.questions = {}
        self.player_scores = {}
        self.question_timers = {}
        self.auto_next_timers = {}
        self.lock = Lock()

    def create_game(self, game_code, title, questions):
        shuffled_questions = []
        for q in questions:
            correct_text = q["options"][q["correct_answer"]]
            options = q["options"].copy()
            random.shuffle(options)
            new_correct = options.index(correct_text)
            shuffled_questions.append({
                "text": q["text"],
                "options": options,
                "correct_answer": new_correct,
                "time_limit": q["time_limit"]
            })
        with self.lock:
            self.games[game_code] = {
                "title": title,
                "status": "waiting",
                "players": [],
                "current_question": 0,
                "scores": {},
                "created_at": datetime.now().isoformat(),
                "host_connected": False,
                "question_active": False,
                "answers": {},
                "question_start_time": None,
                "question_end_time": None,
                "server_start_time": None,
                "server_time_limit": 0,
                "results_shown": False,
                "total_questions": len(shuffled_questions)
            }
            self.questions[game_code] = shuffled_questions
            self.player_scores[game_code] = {}

    def join_game(self, game_code, team_name):
        with self.lock:
            game = self.games.get(game_code)
            if not game or game["status"] != "waiting":
                return False
            existing_player = next((p for p in game["players"] if p["name"] == team_name), None)
            if existing_player:
                if not existing_player["connected"]:
                    existing_player["connected"] = True
                    return True
                else:
                    return False
            player = {
                "id": str(uuid.uuid4()),
                "name": team_name,
                "score": 0,
                "connected": True,
                "last_answer": None,
                "answer_time": None,
                "joined_at": datetime.now().isoformat()
            }
            game["players"].append(player)
            game["scores"][team_name] = 0
            self.player_scores[game_code][team_name] = 0
            return True

    def disconnect_player(self, game_code, player_name):
        with self.lock:
            game = self.games.get(game_code)
            if not game:
                return
            player = next((p for p in game["players"] if p["name"] == player_name), None)
            if player:
                player["connected"] = False

    def get_game(self, game_code):
        return self.games.get(game_code)

    def start_game(self, game_code):
        with self.lock:
            game = self.games.get(game_code)
            if not game or len(game["players"]) == 0:
                return False
            game["status"] = "active"
            return True

    def reset_game(self, game_code):
        with self.lock:
            game = self.games.get(game_code)
            if not game or game['status'] != 'finished':
                return
            if game_code in self.question_timers:
                del self.question_timers[game_code]
            if game_code in self.auto_next_timers:
                del self.auto_next_timers[game_code]

            old = game
            self.games[game_code] = {
                "title": old["title"],
                "status": "waiting",
                "players": [],
                "current_question": 0,
                "scores": {},
                "created_at": datetime.now().isoformat(),
                "host_connected": False,
                "question_active": False,
                "answers": {},
                "question_start_time": None,
                "question_end_time": None,
                "server_start_time": None,
                "server_time_limit": 0,
                "results_shown": False,
                "total_questions": old["total_questions"]
            }
            self.player_scores[game_code] = {}


game_manager = GameManager()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Викторина Kahoot-like</title>
    <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    <style>
        /* Все стили */
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .card { background: white; border-radius: 15px; padding: 30px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); margin-bottom: 20px; }
        h1 { color: #333; margin-bottom: 20px; text-align: center; }
        h2 { color: #444; margin-bottom: 15px; }
        .btn { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 12px 24px; border-radius: 25px; cursor: pointer; font-size: 16px; font-weight: 600; transition: transform 0.2s, box-shadow 0.2s; margin: 5px; }
        .btn-secondary { background: #6c757d; }
        .btn-success { background: #28a745; }
        .btn-danger { background: #dc3545; }
        .btn-warning { background: #ffc107; color: #212529; }
        .btn-info { background: #17a2b8; }
        .input-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; color: #666; font-weight: 600; }
        input, textarea, select { width: 100%; padding: 10px; border: 2px solid #e1e1e1; border-radius: 8px; font-size: 16px; }
        .game-code { font-size: 48px; font-weight: bold; text-align: center; letter-spacing: 10px; color: #333; margin: 20px 0; background: #f8f9fa; padding: 20px; border-radius: 10px; border: 3px dashed #667eea; }
        .players-list { list-style: none; margin-top: 20px; }
        .player-item { background: #f8f9fa; padding: 15px; margin: 5px 0; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; border-left: 4px solid #667eea; }
        .question-container { text-align: center; padding: 40px 20px; }
        .question-text { font-size: 28px; margin-bottom: 30px; color: #333; }
        .options-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin: 30px 0; }
        .option-btn { padding: 25px; font-size: 18px; font-weight: 600; border: none; border-radius: 12px; cursor: pointer; transition: all 0.2s; color: white; }
        .option-btn:hover:not(:disabled) { transform: scale(1.05); box-shadow: 0 10px 20px rgba(0,0,0,0.2); }
        .option-1 { background: #FF6B6B; }
        .option-2 { background: #4ECDC4; }
        .option-3 { background: #FFD166; }
        .option-4 { background: #118AB2; }
        .option-selected { border: 5px solid #333; box-shadow: 0 0 20px rgba(0,0,0,0.3); }
        .synchronized-timer { font-size: 36px; font-weight: bold; text-align: center; margin: 20px 0; padding: 15px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border-radius: 10px; display: inline-block; min-width: 100px; }
        .hidden { display: none !important; }
        .active-view { display: block !important; }
        .status-message { padding: 15px; border-radius: 8px; margin: 10px 0; text-align: center; font-weight: bold; }
        .status-active { background: #d1ecf1; color: #0c5460; border: 1px solid #bee5eb; }
        .status-waiting { background: #fff3cd; color: #856404; border: 1px solid #ffeaa7; }
        .progress-container { margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 10px; }
        .progress-bar { height: 20px; background: #e9ecef; border-radius: 10px; overflow: hidden; }
        .progress-fill { height: 100%; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); transition: width 0.3s; }
        .auto-next-timer { font-size: 24px; text-align: center; margin: 20px 0; padding: 15px; background: #17a2b8; color: white; border-radius: 10px; }
        .error-message { color: #dc3545; background: #f8d7da; border: 1px solid #f5c6cb; padding: 10px; border-radius: 5px; margin: 10px 0; }
        .correct-answer-marker { background-color: #d4edda; border-left: 5px solid #28a745; padding: 10px; margin: 10px 0; border-radius: 5px; }
        .csv-note { font-size: 12px; color: #666; margin-top: 5px; }
        #studentView, #teacherView, #createGameView, #joinGameView, #waitingView, #questionView, #resultsView, #gameOverView, #gameHostView { display: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>🎮 Платформа для викторин</h1>

            <!-- Главное меню -->
            <div id="mainMenuView" class="active-view">
                <div style="text-align: center; padding: 40px;">
                    <h2>Добро пожаловать!</h2>
                    <p style="margin: 20px 0 40px;">Создайте или присоединитесь к игре</p>
                    <button class="btn" onclick="showView('teacherView')">👨‍🏫 Я учитель</button>
                    <button class="btn" onclick="showView('studentView')">👨‍🎓 Я ученик</button>
                </div>
            </div>

            <!-- Меню учителя -->
            <div id="teacherView">
                <h2>Панель учителя</h2>
                <button class="btn" onclick="showView('createGameView')">🎮 Создать новую игру</button>
                <button class="btn btn-secondary" onclick="showView('mainMenuView')">← Назад</button>
            </div>

            <!-- Создание игры -->
            <div id="createGameView">
                <h2>Создание новой игры</h2>

                <!-- НОВАЯ КНОПКА: Импорт из CSV -->
                <div style="margin-bottom: 20px; padding: 15px; background: #e8f4fd; border-radius: 10px;">
                    <h3 style="margin-top: 0;">📊 Импорт вопросов из CSV</h3>
                    <p style="margin-bottom: 10px;">Формат файла: Вопрос,Вариант А,Вариант Б,Вариант В,Вариант Г,Время(сек)</p>
                    <div>
                        <button class="btn btn-info" onclick="document.getElementById('csvFile').click()">
                            📥 Выбрать CSV файл
                        </button>
                        <input type="file" id="csvFile" accept=".csv, .txt" style="display:none" onchange="importFromCSV(event)">
                        <button class="btn btn-secondary" onclick="downloadTemplate()">
                            📋 Скачать пример CSV
                        </button>
                    </div>
                    <div id="csvImportStatus" class="csv-note" style="margin-top: 10px;"></div>
                </div>

                <div class="input-group">
                    <label>Название игры</label>
                    <input type="text" id="gameTitle" placeholder="Введите название" value="Математическая викторина">
                </div>
                <div id="questionsContainer"></div>
                <button class="btn btn-secondary" onclick="addQuestion()">➕ Добавить вопрос</button>
                <button class="btn btn-success" onclick="createGame()">🚀 Создать игру</button>
                <button class="btn" onclick="showView('teacherView')">← Назад</button>
            </div>

            <!-- Комната хоста -->
            <div id="gameHostView" class="hidden">
                <div class="question-counter" id="questionCounter">В: 0/0</div>
                <h2>Управление игрой</h2>
                <div class="game-code" id="gameCodeDisplay">КОД</div>
                <div class="input-group">
                    <label>Поделитесь этим кодом с учениками:</label>
                    <input type="text" id="joinCodeInput" readonly style="font-size: 20px; font-weight: bold; text-align: center;">
                </div>
                <h3>Игроки (<span id="playerCount">0</span>):</h3>
                <ul class="players-list" id="playersList"></ul>
                <div id="gameControls">
                    <button class="btn btn-success" onclick="startGame()" id="startGameBtn">▶ Начать игру</button>
                </div>
                <div id="questionControls" class="hidden">
                    <div class="teacher-controls">
                        <h3>Текущий вопрос</h3>
                        <div class="question-info">
                            <div id="currentQuestionText"></div>
                            <div class="synchronized-timer" id="hostTimer">30</div>
                            <div id="questionStats"></div>
                            <div id="questionStatus" class="status-message status-active">Вопрос идёт...</div>
                        </div>
                        <div class="progress-container">
                            <div class="progress-label">
                                <span>Прогресс</span>
                                <span id="progressText">0/0 ответили</span>
                            </div>
                            <div class="progress-bar">
                                <div class="progress-fill" id="progressFill" style="width: 0%"></div>
                            </div>
                        </div>
                        <div class="action-buttons">
                            <button class="btn btn-warning" onclick="showQuestionResults()" id="showResultsBtn">📊 Показать результаты</button>
                            <button class="btn" onclick="endQuestionEarly()" id="endQuestionBtn">⏹ Завершить вопрос</button>
                            <button class="btn btn-danger" onclick="endGame()" id="endGameBtn">🏁 Завершить игру</button>
                        </div>
                    </div>
                </div>
                <button class="btn btn-secondary" onclick="backToMain()">← В главное меню</button>
            </div>

            <!-- Вход ученика -->
            <div id="studentView">
                <h2>Присоединиться к игре</h2>
                <div class="input-group">
                    <label>Код игры:</label>
                    <input type="text" id="studentGameCode" placeholder="например, ABC123" maxlength="6" style="text-transform: uppercase;">
                </div>
                <div class="input-group">
                    <label>Название команды:</label>
                    <input type="text" id="teamName" placeholder="Введите название команды" value="Команда 1">
                </div>
                <div id="joinError" class="error-message hidden"></div>
                <button class="btn btn-success" onclick="joinGame()">✅ Присоединиться</button>
                <button class="btn" onclick="showView('mainMenuView')">← Назад</button>
            </div>

            <!-- Ожидание начала -->
            <div id="waitingView">
                <h2>Ожидание начала игры...</h2>
                <p>Код игры: <strong id="waitingGameCode"></strong></p>
                <p>Команда: <strong id="waitingTeamName"></strong></p>
                <div class="waiting-message" id="waitingMessage">Ожидание, пока учитель начнёт игру...</div>
                <div class="timer" id="waitingTimer">--</div>
                <div id="waitingPlayers"></div>
                <button class="btn btn-danger" onclick="leaveGame()">← Покинуть</button>
            </div>

            <!-- Экран вопроса -->
            <div id="questionView">
                <div class="question-counter" id="studentQuestionCounter">В: 0/0</div>
                <div class="question-container">
                    <div class="synchronized-timer" id="questionTimer">30</div>
                    <div class="question-text" id="questionText"></div>
                    <div class="options-grid" id="optionsGrid"></div>
                    <div id="questionStatusStudent" class="status-message status-active">Осталось времени...</div>
                    <p id="answerStatus"></p>
                </div>
            </div>

            <!-- Результаты вопроса -->
            <div id="resultsView">
                <h2>Результаты</h2>
                <div class="auto-next-timer" id="autoNextTimer">Следующий вопрос через: <span id="nextQuestionCountdown">7</span> секунд</div>
                <div id="currentResults"></div>
                <div class="leaderboard" id="leaderboard"></div>
                <button class="btn" onclick="showView('mainMenuView')">← В главное меню</button>
            </div>

            <!-- Конец игры -->
            <div id="gameOverView" class="hidden">
                <div class="game-over">
                    <h1>🎉 Игра завершена! 🎉</h1>
                    <div class="winner" id="winnerName"></div>
                    <div id="finalLeaderboard"></div>
                    <button class="btn" onclick="showView('mainMenuView')">← В главное меню</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let socket = null;
        let currentView = 'mainMenuView';
        let gameCode = '';
        let teamName = '';
        let currentQuestion = null;
        let selectedAnswer = null;
        let answerSubmitted = false;
        let waitingInterval = null;
        let serverTimeUpdateInterval = null;
        let serverStartTime = null;
        let serverTimeLimit = 0;
        let autoNextInterval = null;

        function showView(viewId) {
            document.querySelectorAll('[id$="View"]').forEach(v => {
                v.classList.remove('active-view');
                v.classList.add('hidden');
            });
            document.getElementById(viewId).classList.add('active-view');
            currentView = viewId;

            const joinError = document.getElementById('joinError');
            if (joinError) joinError.classList.add('hidden');

            if (viewId !== 'waitingView' && waitingInterval) {
                clearInterval(waitingInterval);
                waitingInterval = null;
            }
            if (viewId !== 'questionView' && viewId !== 'gameHostView' && serverTimeUpdateInterval) {
                clearInterval(serverTimeUpdateInterval);
                serverTimeUpdateInterval = null;
            }
            if (viewId !== 'resultsView' && autoNextInterval) {
                clearInterval(autoNextInterval);
                autoNextInterval = null;
            }

            if (viewId === 'mainMenuView') {
                if (socket) socket.disconnect();
                resetGameState();
            }

            if (viewId === 'gameHostView') {
                document.getElementById('gameControls').classList.remove('hidden');
                document.getElementById('questionControls').classList.add('hidden');
                document.getElementById('startGameBtn').disabled = false;
                document.getElementById('playersList').innerHTML = '';
                document.getElementById('playerCount').textContent = '0';
                document.getElementById('questionCounter').textContent = 'В: 0/0';
            }

            if (viewId === 'createGameView') {
                document.getElementById('questionsContainer').innerHTML = '';
                addQuestion();
                document.getElementById('csvImportStatus').innerHTML = '';
            }
        }

        function resetGameState() {
            gameCode = '';
            teamName = '';
            currentQuestion = null;
            selectedAnswer = null;
            answerSubmitted = false;
            serverStartTime = null;
            serverTimeLimit = 0;
            if (serverTimeUpdateInterval) clearInterval(serverTimeUpdateInterval);
            if (autoNextInterval) clearInterval(autoNextInterval);
            serverTimeUpdateInterval = null;
            autoNextInterval = null;
        }

        function backToMain() {
            if (socket) socket.disconnect();
            resetGameState();
            showView('mainMenuView');
        }

        function leaveGame() {
            if (socket) socket.disconnect();
            resetGameState();
            showView('studentView');
        }

        function addQuestion() {
            const container = document.getElementById('questionsContainer');
            const index = container.children.length + 1;
            const div = document.createElement('div');
            div.className = 'card';
            div.innerHTML = `
                <h3>Вопрос ${index}</h3>
                <div class="input-group"><label>Текст вопроса</label><textarea id="question_${index}" placeholder="Введите вопрос">Сколько будет ${index} + ${index}?</textarea></div>
                <div class="input-group"><label>Вариант 1 (правильный)</label><input type="text" id="option1_${index}" value="${index * 2}"></div>
                <div class="input-group"><label>Вариант 2</label><input type="text" id="option2_${index}" value="${index * 2 - 1}"></div>
                <div class="input-group"><label>Вариант 3</label><input type="text" id="option3_${index}" value="${index * 2 + 1}"></div>
                <div class="input-group"><label>Вариант 4</label><input type="text" id="option4_${index}" value="${index * 3}"></div>
                <div class="input-group"><label>Время на вопрос (секунд)</label><input type="number" id="time_${index}" value="30" min="5" max="120"></div>
                <button class="btn btn-danger" onclick="this.parentElement.remove()">❌ Удалить</button>
            `;
            container.appendChild(div);
        }

        // НОВАЯ ФУНКЦИЯ: Импорт из CSV
        function importFromCSV(event) {
            const file = event.target.files[0];
            if (!file) return;

            const status = document.getElementById('csvImportStatus');
            status.innerHTML = '⏳ Чтение файла...';

            const reader = new FileReader();

            reader.onload = function(e) {
                try {
                    const content = e.target.result;
                    const lines = content.split(/\\r?\\n/);

                    // Определяем разделитель (запятая или точка с запятой)
                    let delimiter = ',';
                    if (lines[0] && lines[0].includes(';')) {
                        delimiter = ';';
                    }

                    const questions = [];

                    // Проходим по всем строкам
                    for (let i = 0; i < lines.length; i++) {
                        const line = lines[i].trim();
                        if (!line) continue;

                        // Пропускаем строку с заголовками если есть
                        if (i === 0 && line.toLowerCase().includes('вопрос')) continue;

                        // Разбиваем строку
                        let values;
                        if (delimiter === ',') {
                            // Простой парсинг для CSV
                            values = line.split(',').map(v => v.trim());
                        } else {
                            values = line.split(';').map(v => v.trim());
                        }

                        // Убираем кавычки если есть
                        values = values.map(v => v.replace(/^"(.*)"$/, '$1'));

                        if (values.length >= 5) {
                            const question = {
                                text: values[0] || 'Вопрос',
                                options: [
                                    values[1] || 'Вариант А',
                                    values[2] || 'Вариант Б',
                                    values[3] || 'Вариант В',
                                    values[4] || 'Вариант Г'
                                ],
                                correct_answer: 0,
                                time_limit: values[5] ? parseInt(values[5]) || 30 : 30
                            };
                            questions.push(question);
                        }
                    }

                    if (questions.length === 0) {
                        throw new Error('Не найдено вопросов в файле');
                    }

                    // Очищаем существующие вопросы
                    document.getElementById('questionsContainer').innerHTML = '';

                    // Добавляем вопросы из CSV
                    questions.forEach((q, index) => {
                        const div = document.createElement('div');
                        div.className = 'card';
                        const i = index + 1;

                        div.innerHTML = `
                            <h3>Вопрос ${i}</h3>
                            <div class="input-group"><label>Текст вопроса</label><textarea id="question_${i}">${q.text}</textarea></div>
                            <div class="input-group"><label>Вариант 1 (правильный)</label><input type="text" id="option1_${i}" value="${q.options[0]}"></div>
                            <div class="input-group"><label>Вариант 2</label><input type="text" id="option2_${i}" value="${q.options[1]}"></div>
                            <div class="input-group"><label>Вариант 3</label><input type="text" id="option3_${i}" value="${q.options[2]}"></div>
                            <div class="input-group"><label>Вариант 4</label><input type="text" id="option4_${i}" value="${q.options[3]}"></div>
                            <div class="input-group"><label>Время на вопрос (секунд)</label><input type="number" id="time_${i}" value="${q.time_limit}" min="5" max="120"></div>
                            <button class="btn btn-danger" onclick="this.parentElement.remove()">❌ Удалить</button>
                        `;

                        document.getElementById('questionsContainer').appendChild(div);
                    });

                    status.innerHTML = `✅ Загружено ${questions.length} вопросов из CSV`;

                    // Очищаем input
                    event.target.value = '';

                } catch (error) {
                    status.innerHTML = '❌ Ошибка: ' + error.message;
                    console.error(error);
                }
            };

            reader.readAsText(file, 'UTF-8');
        }

        // НОВАЯ ФУНКЦИЯ: Скачать шаблон CSV
        function downloadTemplate() {
            const template = [
                'Вопрос,Вариант А,Вариант Б,Вариант В,Вариант Г,Время',
                '"Столица Франции?",Париж,Лондон,Берлин,Мадрид,30',
                '"Сколько будет 2 + 2?",3,4,5,6,20',
                '"Самая высокая гора?",Эверест,К2,Канченджанга,Лхоцзе,25'
            ].join('\\n');

            const blob = new Blob(['\\uFEFF' + template], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'template_questions.csv';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        function createGame() {
            const title = document.getElementById('gameTitle').value;
            if (!title) { alert('Введите название игры'); return; }
            const questions = [];
            const containers = document.querySelectorAll('#questionsContainer > div');
            if (containers.length === 0) { alert('Добавьте хотя бы один вопрос'); return; }
            for (let i = 0; i < containers.length; i++) {
                const idx = i + 1;
                const qText = document.getElementById(`question_${idx}`)?.value;
                const o1 = document.getElementById(`option1_${idx}`)?.value;
                const o2 = document.getElementById(`option2_${idx}`)?.value;
                const o3 = document.getElementById(`option3_${idx}`)?.value;
                const o4 = document.getElementById(`option4_${idx}`)?.value;
                if (!qText || !o1 || !o2 || !o3 || !o4) {
                    alert(`Заполните все поля для вопроса ${idx}`); return;
                }
                questions.push({
                    text: qText,
                    options: [o1, o2, o3, o4],
                    correct_answer: 0,
                    time_limit: parseInt(document.getElementById(`time_${idx}`).value) || 30
                });
            }
            fetch('/api/create_game', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ title, questions })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    resetGameState();
                    gameCode = data.game_code;
                    document.getElementById('gameControls').classList.remove('hidden');
                    document.getElementById('questionControls').classList.add('hidden');
                    document.getElementById('startGameBtn').disabled = false;
                    document.getElementById('playersList').innerHTML = '';
                    document.getElementById('playerCount').textContent = '0';
                    document.getElementById('gameCodeDisplay').textContent = gameCode;
                    document.getElementById('joinCodeInput').value = gameCode;
                    showView('gameHostView');
                    connectSocket(gameCode, null, 'teacher');
                } else alert(data.message);
            })
            .catch(err => { console.error(err); alert('Ошибка создания игры'); });
        }

        function joinGame() {
            gameCode = document.getElementById('studentGameCode').value.trim().toUpperCase();
            teamName = document.getElementById('teamName').value.trim();
            if (!gameCode || gameCode.length < 3) { alert('Введите корректный код игры'); return; }
            if (!teamName) { alert('Введите название команды'); return; }
            fetch('/api/join_game', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ game_code: gameCode, team_name: teamName })
            })
            .then(r => {
                if (!r.ok) return r.json().then(err => { throw new Error(err.detail || 'Ошибка'); });
                return r.json();
            })
            .then(data => {
                if (data.success) {
                    showView('waitingView');
                    document.getElementById('waitingGameCode').textContent = gameCode;
                    document.getElementById('waitingTeamName').textContent = teamName;
                    connectSocket(gameCode, teamName, 'student');
                    startWaitingTimer();
                } else alert(data.message);
            })
            .catch(err => { alert(err.message); });
        }

        function startWaitingTimer() {
            let sec = 0;
            waitingInterval = setInterval(() => {
                sec++;
                document.getElementById('waitingTimer').textContent = formatTime(sec);
            }, 1000);
        }

        function formatTime(seconds) {
            const m = Math.floor(seconds / 60);
            const s = seconds % 60;
            return `${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}`;
        }

        function connectSocket(code, name, role) {
            if (socket) socket.disconnect();
            socket = io();
            socket.on('connect', () => {
                if (role === 'teacher') socket.emit('teacher_join', { game_code: code });
                else socket.emit('player_join', { game_code: code, player_name: name });
            });
            socket.on('message', handleSocketMessage);
            socket.on('disconnect', () => {
                if (['waitingView','questionView','resultsView'].includes(currentView)) {
                    setTimeout(() => connectSocket(code, name, role), 3000);
                }
            });
        }

        function handleSocketMessage(data) {
            switch(data.type) {
                case 'player_joined':
                    updatePlayersList(data.players);
                    if (currentView === 'waitingView') updateWaitingPlayers(data.players);
                    break;
                case 'player_left':
                    updatePlayersList(data.players);
                    if (currentView === 'waitingView') updateWaitingPlayers(data.players);
                    break;
                case 'game_started':
                    if (currentView === 'waitingView') {
                        document.getElementById('waitingMessage').textContent = 'Игра началась! Приготовьтесь...';
                        setTimeout(() => { if (data.question) showQuestion(data.question); }, 2000);
                    }
                    break;
                case 'show_question':
                    showQuestion(data.question);
                    break;
                case 'server_time_update':
                    handleServerTimeUpdate(data.server_time, data.time_limit, data.start_time);
                    break;
                case 'question_ended':
                    if (currentView === 'questionView') {
                        disableQuestionButtons();
                        document.getElementById('questionStatusStudent').textContent = 'Время вышло! Ожидание результатов...';
                    }
                    break;
                case 'show_results':
                    showResults(data.results);
                    if (data.is_last_question) {
                        document.getElementById('autoNextTimer').innerHTML = '<strong>Это был последний вопрос!</strong>';
                        clearInterval(autoNextInterval);
                        autoNextInterval = null;
                        setTimeout(() => showFinalResults(data.final_results), 5000);
                    } else startAutoNextTimer();
                    break;
                case 'game_over':
                    showFinalResults(data.final_results);
                    break;
                case 'game_ended':
                    if (socket) socket.disconnect();
                    resetGameState();
                    showView('mainMenuView');
                    break;
                case 'answer_received':
                    document.getElementById('answerStatus').textContent = 'Ответ принят! Ожидание результатов...';
                    document.getElementById('answerStatus').style.color = '#28a745';
                    break;
                case 'error':
                    alert('Ошибка: ' + data.message);
                    if (data.message.includes('not found')) showView('studentView');
                    break;
                case 'question_started':
                    if (currentView === 'gameHostView') {
                        document.getElementById('questionCounter').textContent = `В: ${data.question_number}/${data.total_questions}`;
                        document.getElementById('currentQuestionText').textContent = data.question_text;
                        updateQuestionStats(data.answers_received, data.total_players);
                        document.getElementById('questionStatus').textContent = 'Вопрос идёт...';
                        handleServerTimeUpdate(data.server_time, data.time_limit, data.start_time);
                    }
                    break;
                case 'question_stats_update':
                    if (currentView === 'gameHostView') updateQuestionStats(data.answers_received, data.total_players);
                    break;
                case 'auto_next_countdown':
                    if (currentView === 'resultsView') document.getElementById('nextQuestionCountdown').textContent = data.seconds_left;
                    break;
            }
        }

        function startGame() {
            if (socket && socket.connected) {
                socket.emit('host_message', { type: 'start_game', game_code: gameCode });
                document.getElementById('gameControls').classList.add('hidden');
                document.getElementById('questionControls').classList.remove('hidden');
                document.getElementById('startGameBtn').disabled = true;
            } else alert('Сокет не подключён');
        }

        function endGame() {
            if (socket && socket.connected) {
                socket.emit('host_message', { type: 'end_game', game_code: gameCode });
            }
        }

        function showQuestionResults() {
            if (socket && socket.connected) {
                socket.emit('host_message', { type: 'show_question_results', game_code: gameCode });
            }
        }

        function endQuestionEarly() {
            if (socket && socket.connected) {
                socket.emit('host_message', { type: 'end_question_early', game_code: gameCode });
            }
        }

        function showQuestion(question) {
            currentQuestion = question;
            selectedAnswer = null;
            answerSubmitted = false;
            showView('questionView');
            document.getElementById('questionText').textContent = question.text;
            document.getElementById('studentQuestionCounter').textContent = `В: ${question.question_number}/${question.total_questions}`;
            document.getElementById('answerStatus').textContent = '';
            document.getElementById('questionStatusStudent').textContent = 'Осталось времени...';
            const grid = document.getElementById('optionsGrid');
            grid.innerHTML = '';
            question.options.forEach((opt, idx) => {
                const btn = document.createElement('button');
                btn.className = `option-btn option-${idx+1}`;
                btn.textContent = `${String.fromCharCode(65+idx)}: ${opt}`;
                btn.onclick = () => selectAnswer(idx);
                grid.appendChild(btn);
            });
        }

        function disableQuestionButtons() {
            document.querySelectorAll('.option-btn').forEach(b => b.disabled = true);
        }

        function selectAnswer(index) {
            if (answerSubmitted) return;
            selectedAnswer = index;
            document.querySelectorAll('.option-btn').forEach((btn, i) => {
                btn.classList.remove('option-selected');
                if (i === index) btn.classList.add('option-selected');
            });
            if (socket && socket.connected) {
                answerSubmitted = true;
                const elapsed = (Date.now() - serverStartTime) / 1000;
                const timeLeft = Math.max(0, Math.floor(serverTimeLimit - elapsed));
                socket.emit('submit_answer', {
                    game_code: gameCode,
                    player_name: teamName,
                    answer: index,
                    time_left: timeLeft
                });
                document.getElementById('answerStatus').textContent = 'Ответ отправлен!';
                document.getElementById('answerStatus').style.color = '#28a745';
                disableQuestionButtons();
            }
        }

        function handleServerTimeUpdate(serverTime, timeLimit, startTime) {
            serverStartTime = startTime;
            serverTimeLimit = timeLimit;
            if (serverTimeUpdateInterval) clearInterval(serverTimeUpdateInterval);
            const update = () => {
                if (!serverStartTime || !serverTimeLimit) return;
                const elapsed = (Date.now() - serverStartTime) / 1000;
                let left = Math.max(0, Math.floor(serverTimeLimit - elapsed));
                const timer = document.getElementById('questionTimer');
                const hostTimer = document.getElementById('hostTimer');
                if (timer) timer.textContent = left;
                if (hostTimer) hostTimer.textContent = left;
                if (left <= 0) {
                    clearInterval(serverTimeUpdateInterval);
                    serverTimeUpdateInterval = null;
                }
            };
            update();
            serverTimeUpdateInterval = setInterval(update, 100);
        }

        function updateQuestionStats(received, total) {
            document.getElementById('questionStats').innerHTML = `<p>Ответов: ${received}/${total}</p>`;
            document.getElementById('progressText').textContent = `${received}/${total} ответили`;
            document.getElementById('progressFill').style.width = total ? `${(received/total)*100}%` : '0%';
        }

        function updatePlayersList(players) {
            const connectedPlayers = players.filter(p => p.connected);
            const list = document.getElementById('playersList');
            list.innerHTML = '';
            connectedPlayers.forEach(p => {
                const li = document.createElement('li');
                li.className = 'player-item';
                li.innerHTML = `<span class="player-name">${p.name}</span><span class="player-score">${p.score || 0} очков</span>`;
                list.appendChild(li);
            });
            document.getElementById('playerCount').textContent = connectedPlayers.length;
        }

        function updateWaitingPlayers(players) {
            const connectedPlayers = players.filter(p => p.connected);
            const div = document.getElementById('waitingPlayers');
            div.innerHTML = `<h3>Игроков (${connectedPlayers.length}):</h3><ul class="players-list">${
                connectedPlayers.map(p => `<li class="player-item"><span class="player-name">${p.name}</span><span class="player-score">${p.score||0}</span></li>`).join('')
            }</ul>`;
        }

        function startAutoNextTimer() {
            if (autoNextInterval) clearInterval(autoNextInterval);
            let left = 7;
            document.getElementById('nextQuestionCountdown').textContent = left;
            autoNextInterval = setInterval(() => {
                left--;
                document.getElementById('nextQuestionCountdown').textContent = left;
                if (left <= 0) { clearInterval(autoNextInterval); autoNextInterval = null; }
            }, 1000);
        }

        function showResults(results) {
            showView('resultsView');
            let html = `<h3>Результаты вопроса ${currentQuestion?.question_number || '?'}</h3>`;
            if (results.correct_answer !== undefined && currentQuestion) {
                html += `<div class="correct-answer-marker">✅ Правильный ответ: ${currentQuestion.options[results.correct_answer]}</div>`;
            }
            if (results.answers) {
                html += '<div class="results-grid">';
                results.answers.forEach(a => {
                    const isCorrect = a.answer === results.correct_answer;
                    const isUs = a.team === teamName;
                    const border = isUs ? (isCorrect ? '#28a745' : '#dc3545') : '#667eea';
                    html += `<div class="team-card" style="border-top-color:${border}"><strong>${a.team}${isUs?' (Вы)':''}</strong><p>Ответ: ${a.answer_text}</p><p style="color:${isCorrect?'#28a745':'#dc3545'}">${isCorrect?'✓ Верно':'✗ Неверно'}</p><p>Очки: +${a.points_earned}</p><p>Всего: ${a.total_score}</p></div>`;
                });
                html += '</div>';
            }
            document.getElementById('currentResults').innerHTML = html;
            if (results.leaderboard) {
                let lb = '<h3>Таблица лидеров</h3>';
                results.leaderboard.forEach((p,i) => {
                    const isUs = p.name === teamName;
                    lb += `<div class="leaderboard-item ${i<3?'rank-'+(i+1):''}" style="${isUs?'background:#e3f2fd':''}">${i+1}. ${p.name}${isUs?' (Вы)':''} <span class="player-score">${p.score} очков</span></div>`;
                });
                document.getElementById('leaderboard').innerHTML = lb;
            }
        }

        function showFinalResults(results) {
            showView('gameOverView');
            if (results.length) {
                document.getElementById('winnerName').textContent = `🏆 Победитель: ${results[0].name} 🏆`;
                let lb = '<div class="leaderboard">';
                results.forEach((p,i) => {
                    const isUs = p.name === teamName;
                    lb += `<div class="leaderboard-item ${i<3?'rank-'+(i+1):''}" style="${isUs?'background:#e3f2fd;font-weight:bold':''}">${i+1}. ${p.name}${isUs?' (Вы)':''} <span class="player-score">${p.score} очков</span></div>`;
                });
                lb += '</div>';
                document.getElementById('finalLeaderboard').innerHTML = lb;
            }
        }

        window.onload = function() {
            document.getElementById('questionsContainer').innerHTML = '';
            addQuestion();
        };
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/create_game', methods=['POST'])
def api_create_game():
    data = request.json
    game_code = str(uuid.uuid4())[:6].upper()
    while game_code in game_manager.games:
        game_code = str(uuid.uuid4())[:6].upper()
    game_manager.create_game(game_code, data['title'], data['questions'])
    return jsonify(success=True, game_code=game_code)


@app.route('/api/join_game', methods=['POST'])
def api_join_game():
    data = request.json
    game_code = data.get('game_code', '').upper().strip()
    team_name = data.get('team_name', '').strip()
    if not game_code or not team_name:
        return jsonify(success=False, message='Не хватает данных'), 400
    if game_manager.join_game(game_code, team_name):
        return jsonify(success=True)
    game = game_manager.get_game(game_code)
    if not game:
        return jsonify(success=False, message='Игра не найдена'), 404
    if game['status'] != 'waiting':
        return jsonify(success=False, message='Игра уже началась'), 400
    return jsonify(success=False, message='Имя команды уже занято или игра недоступна'), 400


@app.route('/api/game/<game_code>/status')
def game_status(game_code):
    game = game_manager.get_game(game_code.upper())
    if not game:
        return jsonify(success=False, message='Игра не найдена'), 404
    return jsonify(success=True, game={
        'title': game['title'],
        'status': game['status'],
        'players': game['players'],
        'current_question': game['current_question'],
        'total_questions': game.get('total_questions', 0)
    })


sid_to_player = {}


@socketio.on('teacher_join')
def handle_teacher_join(data):
    game_code = data['game_code']
    game = game_manager.get_game(game_code)
    if not game:
        emit('message', {'type': 'error', 'message': 'Игра не найдена'})
        return
    game['host_connected'] = True
    join_room(game_code)
    sid_to_player[request.sid] = (game_code, 'host')
    emit('message', {'type': 'connected', 'game_code': game_code})


@socketio.on('player_join')
def handle_player_join(data):
    game_code = data['game_code']
    player_name = data['player_name']
    game = game_manager.get_game(game_code)
    if not game:
        emit('message', {'type': 'error', 'message': 'Игра не найдена'})
        return
    player = next((p for p in game['players'] if p['name'] == player_name), None)
    if not player:
        emit('message', {'type': 'error', 'message': 'Игрок не зарегистрирован'})
        return

    player['connected'] = True
    join_room(game_code)
    sid_to_player[request.sid] = (game_code, player_name)

    emit('message', {'type': 'player_joined', 'player': player_name, 'players': game['players']}, room=game_code)
    if game['status'] == 'active' and game.get('question_active'):
        questions = game_manager.questions[game_code]
        q_idx = game['current_question']
        if q_idx < len(questions):
            q = questions[q_idx]
            emit('message', {
                'type': 'server_time_update',
                'server_time': int(time.time() * 1000),
                'time_limit': q['time_limit'],
                'start_time': game.get('server_start_time', int(time.time() * 1000))
            })
            emit('message', {
                'type': 'show_question',
                'question': {
                    'text': q['text'],
                    'options': q['options'],
                    'correct_answer': q['correct_answer'],
                    'time_limit': q['time_limit'],
                    'question_number': q_idx + 1,
                    'total_questions': len(questions)
                }
            })


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in sid_to_player:
        game_code, player_name = sid_to_player[sid]
        if player_name == 'host':
            game = game_manager.get_game(game_code)
            if game:
                game['host_connected'] = False
        else:
            game_manager.disconnect_player(game_code, player_name)
            game = game_manager.get_game(game_code)
            if game:
                emit('message', {'type': 'player_left', 'player': player_name, 'players': game['players']},
                     room=game_code)
        del sid_to_player[sid]


@socketio.on('host_message')
def handle_host_message(data):
    game_code = data.get('game_code')
    if not game_code:
        emit('message', {'type': 'error', 'message': 'Нет кода игры'})
        return
    game = game_manager.get_game(game_code)
    if not game:
        emit('message', {'type': 'error', 'message': 'Игра не найдена'})
        return
    msg_type = data.get('type')
    if msg_type == 'start_game':
        if len(game['players']) == 0:
            emit('message', {'type': 'error', 'message': 'Нет игроков'})
            return
        if game_manager.start_game(game_code):
            game['status'] = 'active'
            game['current_question'] = 0
            emit('message', {'type': 'game_started', 'message': 'Игра начинается...'}, room=game_code)
            socketio.start_background_task(show_question_to_all, game_code)
    elif msg_type == 'show_question_results':
        game['question_active'] = False
        socketio.start_background_task(calculate_and_send_results, game_code, True)
    elif msg_type == 'end_question_early':
        game['question_active'] = False
        socketio.start_background_task(calculate_and_send_results, game_code, True)
    elif msg_type == 'end_game':
        emit('message', {'type': 'game_ended'}, room=game_code)
        socketio.sleep(0.5)
        game_manager.reset_game(game_code)


@socketio.on('submit_answer')
def handle_submit_answer(data):
    game_code = data.get('game_code')
    player_name = data.get('player_name')
    answer_index = data.get('answer')
    time_left = data.get('time_left')
    if not game_code or not player_name:
        emit('message', {'type': 'error', 'message': 'Не хватает данных'})
        return
    game = game_manager.get_game(game_code)
    if not game:
        emit('message', {'type': 'error', 'message': 'Игра не найдена'})
        return
    if not game.get('question_active'):
        emit('message', {'type': 'error', 'message': 'Время ответа истекло'})
        return
    player = next((p for p in game['players'] if p['name'] == player_name), None)
    if not player:
        emit('message', {'type': 'error', 'message': 'Игрок не найден'})
        return
    game['answers'][player_name] = {
        'answer': answer_index,
        'time_left': time_left,
        'timestamp': time.time()
    }
    player['last_answer'] = answer_index
    player['answer_time'] = time_left
    emit('message', {'type': 'answer_received'})
    # Обновить статистику для учителя
    answers_received = len(game['answers'])
    total_players = len([p for p in game['players'] if p['connected']])
    emit('message', {
        'type': 'question_stats_update',
        'answers_received': answers_received,
        'total_players': total_players
    }, room=game_code)


def show_question_to_all(game_code):
    game = game_manager.get_game(game_code)
    if not game: return
    questions = game_manager.questions[game_code]
    q_idx = game['current_question']
    if q_idx >= len(questions): return
    q = questions[q_idx]
    game['question_active'] = True
    game['answers'] = {}
    game['question_start_time'] = time.time()
    game['server_start_time'] = int(time.time() * 1000)
    game['server_time_limit'] = q['time_limit']
    game['results_shown'] = False
    socketio.emit('message', {
        'type': 'show_question',
        'question': {
            'text': q['text'],
            'options': q['options'],
            'correct_answer': q['correct_answer'],
            'time_limit': q['time_limit'],
            'question_number': q_idx + 1,
            'total_questions': len(questions)
        }
    }, room=game_code)
    answers_received = len(game['answers'])
    total_players = len([p for p in game['players'] if p['connected']])
    socketio.emit('message', {
        'type': 'question_started',
        'question_text': q['text'],
        'question_number': q_idx + 1,
        'total_questions': len(questions),
        'time_limit': q['time_limit'],
        'start_time': game['server_start_time'],
        'server_time': int(time.time() * 1000),
        'answers_received': answers_received,
        'total_players': total_players
    }, room=game_code)
    game_manager.question_timers[game_code] = socketio.start_background_task(
        question_timer_with_auto_results, game_code, q['time_limit']
    )


def question_timer_with_auto_results(game_code, time_limit):
    game = game_manager.get_game(game_code)
    if not game: return
    start = time.time()
    end = start + time_limit
    server_start = game.get('server_start_time', int(time.time() * 1000))
    while time.time() < end and game.get('question_active', True):
        socketio.sleep(0.5)
        if not game.get('question_active'):
            break
        socketio.emit('message', {
            'type': 'server_time_update',
            'server_time': int(time.time() * 1000),
            'time_limit': time_limit,
            'start_time': server_start
        }, room=game_code)
    if game.get('question_active'):
        game['question_active'] = False
        socketio.emit('message', {'type': 'question_ended'}, room=game_code)
        socketio.emit('message', {'type': 'question_completed'}, room=game_code)
        socketio.sleep(2)
        calculate_and_send_results(game_code)


def calculate_and_send_results(game_code, is_manual=False):
    game = game_manager.get_game(game_code)
    if not game: return
    questions = game_manager.questions[game_code]
    q_idx = game['current_question']
    if q_idx >= len(questions): return
    q = questions[q_idx]
    results = {
        'question': q['text'],
        'correct_answer': q['correct_answer'],
        'answers': [],
        'leaderboard': [],
        'is_last_question': q_idx + 1 >= len(questions)
    }
    for player in game['players']:
        ans = game['answers'].get(player['name'])
        if ans:
            is_correct = ans['answer'] == q['correct_answer']
            points = 0
            if is_correct and ans['answer'] >= 0:
                base = 100
                bonus = int((ans['time_left'] / q['time_limit']) * 500)
                points = base + bonus
                player['score'] += points
            results['answers'].append({
                'team': player['name'],
                'answer': ans['answer'],
                'answer_text': q['options'][ans['answer']] if 0 <= ans['answer'] < len(q['options']) else 'Нет ответа',
                'correct': is_correct,
                'points_earned': points,
                'total_score': player['score'],
                'time_left': ans['time_left']
            })
        else:
            results['answers'].append({
                'team': player['name'],
                'answer': -1,
                'answer_text': 'Нет ответа',
                'correct': False,
                'points_earned': 0,
                'total_score': player['score'],
                'time_left': 0
            })
    for p in game['players']:
        results['leaderboard'].append({'name': p['name'], 'score': p['score']})
    results['leaderboard'].sort(key=lambda x: x['score'], reverse=True)
    final_results = [{'name': p['name'], 'score': p['score']} for p in game['players']]
    final_results.sort(key=lambda x: x['score'], reverse=True)
    socketio.emit('message', {
        'type': 'show_results',
        'results': results,
        'final_results': final_results,
        'is_last_question': results['is_last_question']
    }, room=game_code)
    if not results['is_last_question']:
        for sec in range(7, 0, -1):
            socketio.sleep(1)
            socketio.emit('message', {'type': 'auto_next_countdown', 'seconds_left': sec}, room=game_code)
        socketio.sleep(1)
        game['current_question'] += 1
        game['question_active'] = False
        game['results_shown'] = True
        show_question_to_all(game_code)
    else:
        game['status'] = 'finished'
        game['question_active'] = False
        socketio.sleep(5)
        socketio.emit('message', {'type': 'game_over', 'final_results': final_results}, room=game_code)
        socketio.sleep(10)
        current_game = game_manager.get_game(game_code)
        if current_game and current_game['status'] == 'finished':
            game_manager.reset_game(game_code)


if __name__ == '__main__':
    print("Запуск платформы для викторин на Flask...")
    print("Сервер доступен по адресу http://localhost:5000")
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
