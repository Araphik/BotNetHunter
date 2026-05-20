document.addEventListener('DOMContentLoaded', function() {
    const analyzeForm = document.getElementById('analyzeForm');
    const analyzeBtn = document.getElementById('analyzeBtn');
    const analyzeSpinner = document.getElementById('analyzeSpinner');
    const analyzeBtnText = document.getElementById('analyzeBtnText');
    const analysisStatusText = document.getElementById('analysisStatusText');

    if (!analyzeForm || !analyzeBtn || !analyzeSpinner || !analyzeBtnText || !analysisStatusText) {
        return;
    }

    let pendingAnalysisId = analyzeForm.dataset.pendingAnalysisId || null;
    let statusTimer = null;

    function setAnalysisPending() {
        analyzeBtn.disabled = true;
        analyzeBtn.classList.add('opacity-50');
        analyzeSpinner.classList.remove('d-none');
        analyzeBtnText.textContent = 'Анализ выполняется...';
        analysisStatusText.classList.remove('d-none');
    }

    function setAnalysisReady() {
        analyzeBtn.disabled = false;
        analyzeBtn.classList.remove('opacity-50');
        analyzeSpinner.classList.add('d-none');
        analyzeBtnText.textContent = 'Запустить проверку';
        analysisStatusText.classList.add('d-none');
    }

    async function pollAnalysisStatus() {
        if (!pendingAnalysisId) {
            return;
        }
        try {
            const response = await fetch(`/analysis/status/${pendingAnalysisId}?_=${Date.now()}`, {
                cache: 'no-store',
                headers: {
                    'Accept': 'application/json',
                    'Cache-Control': 'no-cache'
                },
                credentials: 'same-origin'
            });
            if (!response.ok) {
                if (response.status === 401 || response.status === 404) {
                    window.location.reload();
                }
                return;
            }
            const data = await response.json();
            if (data.status === 'completed') {
                pendingAnalysisId = null;
                clearInterval(statusTimer);
                if (data.result_url) {
                    window.location.assign(data.result_url);
                } else {
                    window.location.reload();
                }
                return;
            }
            if (data.status === 'failed') {
                pendingAnalysisId = null;
                clearInterval(statusTimer);
                setAnalysisReady();
                window.location.reload();
            }
        } catch (error) {
            // При временной сетевой ошибке оставляем polling активным.
        }
    }

    if (pendingAnalysisId) {
        setAnalysisPending();
        statusTimer = setInterval(pollAnalysisStatus, 5000);
        setTimeout(pollAnalysisStatus, 1000);
    }

    document.addEventListener('visibilitychange', function() {
        if (!document.hidden && pendingAnalysisId) {
            pollAnalysisStatus();
        }
    });

    analyzeForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const targetInput = document.getElementById('target');
        const value = targetInput.value.trim();
        if (!value) {
            targetInput.classList.add('is-invalid');
            return;
        }
        targetInput.value = value;

        setAnalysisPending();

        try {
            const response = await fetch(analyzeForm.action, {
                method: 'POST',
                body: new FormData(analyzeForm),
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'fetch'
                },
                credentials: 'same-origin'
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.detail || 'Не удалось запустить анализ');
            }
            pendingAnalysisId = data.id;
            analyzeForm.dataset.pendingAnalysisId = String(data.id);
            clearInterval(statusTimer);
            statusTimer = setInterval(pollAnalysisStatus, 5000);
            setTimeout(pollAnalysisStatus, 1000);
        } catch (error) {
            setAnalysisReady();
            alert(error.message);
        }
    });

    const avatarForm = document.getElementById('avatarUploadForm');
    const avatarUploadId = document.getElementById('avatarUploadId');
    const avatarProgress = document.getElementById('avatarUploadProgress');
    const avatarProgressBar = document.getElementById('avatarUploadProgressBar');

    function setAvatarProgress(percent) {
        if (!avatarProgress || !avatarProgressBar) {
            return;
        }
        const value = Math.max(0, Math.min(100, Math.round(percent)));
        avatarProgress.classList.remove('d-none');
        avatarProgressBar.style.width = `${value}%`;
        avatarProgressBar.setAttribute('aria-valuenow', String(value));
    }

    if (avatarForm && avatarUploadId) {
        avatarForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const uploadId = (window.crypto && crypto.randomUUID)
                ? crypto.randomUUID()
                : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
            avatarUploadId.value = uploadId;
            setAvatarProgress(0);

            let socket = null;
            const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
            try {
                socket = new WebSocket(`${protocol}://${window.location.host}/ws/uploads/${uploadId}`);
                socket.addEventListener('open', function() {
                    socket.send('start');
                });
                socket.addEventListener('message', function(event) {
                    const data = JSON.parse(event.data);
                    if (typeof data.percent === 'number') {
                        setAvatarProgress(data.percent);
                    }
                });
            } catch (error) {
                socket = null;
            }

            const request = new XMLHttpRequest();
            request.open('POST', avatarForm.action);
            request.responseType = 'json';
            request.setRequestHeader('Accept', 'application/json');
            request.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

            request.upload.addEventListener('progress', function(event) {
                if (event.lengthComputable) {
                    setAvatarProgress(Math.min(85, (event.loaded / event.total) * 85));
                }
            });

            request.addEventListener('load', function() {
                if (socket) {
                    socket.close();
                }
                if (request.status >= 200 && request.status < 300) {
                    setAvatarProgress(100);
                    window.location.assign('/dashboard?avatar_updated=1');
                    return;
                }
                const detail = request.response && request.response.detail
                    ? request.response.detail
                    : 'Ошибка загрузки файла.';
                alert(detail);
                avatarProgress.classList.add('d-none');
                avatarProgressBar.style.width = '0%';
            });

            request.addEventListener('error', function() {
                if (socket) {
                    socket.close();
                }
                alert('Ошибка загрузки файла.');
                avatarProgress.classList.add('d-none');
                avatarProgressBar.style.width = '0%';
            });

            request.send(new FormData(avatarForm));
        });
    }

    document.querySelectorAll('.bg-orange').forEach(el => {
        el.style.backgroundColor = '#fd7e14';
    });
});
