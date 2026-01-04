// app.js
// 主要的 JavaScript 逻辑：处理 WebSocket 连接、音频采集和播放

const statusDiv = document.getElementById("status");
const messagesDiv = document.getElementById("messages");
const permissionHint = document.getElementById("permissionHint");
const textInput = document.getElementById("textInput");
const sendTextBtn = document.getElementById("sendTextBtn");

let socket = null;
let audioContext = null;
let mediaStream = null;
let micWorkletNode = null;
let ttsWorkletNode = null;

let isTTSPlaying = false;
let ignoreIncomingTTS = false;

let chatHistory = [];
let typingUser = "";
let typingAssistant = "";

// 批处理配置
const BATCH_SAMPLES = 2048;
const HEADER_BYTES = 8;
const FRAME_BYTES = BATCH_SAMPLES * 2;
const MESSAGE_BYTES = HEADER_BYTES + FRAME_BYTES;

const bufferPool = [];
let batchBuffer = null;
let batchView = null;
let batchInt16 = null;
let batchOffset = 0;

function initBatch() {
  if (!batchBuffer) {
    batchBuffer = bufferPool.pop() || new ArrayBuffer(MESSAGE_BYTES);
    batchView = new DataView(batchBuffer);
    batchInt16 = new Int16Array(batchBuffer, HEADER_BYTES);
    batchOffset = 0;
  }
}

function flushBatch() {
  const ts = Date.now() & 0xFFFFFFFF;
  batchView.setUint32(0, ts, false);
  const flags = isTTSPlaying ? 1 : 0;
  batchView.setUint32(4, flags, false);

  socket.send(batchBuffer);

  bufferPool.push(batchBuffer);
  batchBuffer = null;
}

function flushRemainder() {
  if (batchOffset > 0) {
    for (let i = batchOffset; i < BATCH_SAMPLES; i++) {
      batchInt16[i] = 0;
    }
    flushBatch();
  }
}

function initAudioContext() {
  if (!audioContext) {
    try {
      audioContext = new AudioContext({ sampleRate: 24000 });
      console.log("AudioContext 已创建，采样率:", audioContext.sampleRate, "状态:", audioContext.state);
      
      // 如果 AudioContext 被暂停（某些浏览器需要用户交互后才能播放），尝试恢复
      if (audioContext.state === 'suspended') {
        console.log("AudioContext 处于暂停状态，需要用户交互后才能播放音频");
        // 不在这里恢复，等收到音频数据时再恢复
      }
    } catch (err) {
      console.error("创建 AudioContext 失败:", err);
      throw err;
    }
  }
  return audioContext;
}

function base64ToInt16Array(b64) {
  const raw = atob(b64);
  const buf = new ArrayBuffer(raw.length);
  const view = new Uint8Array(buf);
  for (let i = 0; i < raw.length; i++) {
    view[i] = raw.charCodeAt(i);
  }
  return new Int16Array(buf);
}

async function checkMicrophonePermission() {
  /** 检查麦克风权限状态 */
  try {
    const permissionStatus = await navigator.permissions.query({ name: 'microphone' });
    return permissionStatus.state; // 'granted', 'denied', or 'prompt'
  } catch (e) {
    // 某些浏览器不支持 permissions API
    return 'unknown';
  }
}

async function startRawPcmCapture() {
  try {
    // 检查是否支持 getUserMedia
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      // 不显示错误，因为已经在 startBtn.onclick 中处理过了
      console.warn("浏览器不支持 getUserMedia，跳过音频采集");
      return;
    }

    // 检查协议（某些浏览器要求 HTTPS 或 localhost）
    const isSecure = window.location.protocol === 'https:' || 
                     window.location.hostname === 'localhost' || 
                     window.location.hostname === '127.0.0.1';
    
    if (!isSecure) {
      const errorMsg = "为了访问麦克风，需要使用 HTTPS 或 localhost。当前协议: " + window.location.protocol;
      statusDiv.textContent = errorMsg;
      console.warn(errorMsg);
      // 不阻止继续，让用户尝试
    }

    // 检查权限状态
    const permissionState = await checkMicrophonePermission();
    if (permissionState === 'denied') {
      const errorMsg = "麦克风权限已被拒绝。请在浏览器设置中允许麦克风访问，然后刷新页面。";
      statusDiv.textContent = errorMsg;
      if (permissionHint) {
        permissionHint.style.display = 'block';
        permissionHint.style.background = '#f8d7da';
        permissionHint.style.color = '#721c24';
        permissionHint.innerHTML = '<strong>错误：</strong>麦克风权限已被拒绝。请点击地址栏的锁图标，允许麦克风访问，然后刷新页面。';
      }
      alert(errorMsg + "\n\nChrome: 设置 > 隐私和安全 > 网站设置 > 麦克风\nFirefox: 设置 > 隐私与安全 > 权限 > 麦克风");
      return;
    }
    
    // 隐藏提示（如果之前显示过）
    if (permissionHint) {
      permissionHint.style.display = 'none';
    }

    statusDiv.textContent = "正在请求麦克风权限...";
    
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: { ideal: 24000 },
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true
      }
    });
    
    mediaStream = stream;
    initAudioContext();
    
    statusDiv.textContent = "正在初始化音频处理器...";
    await audioContext.audioWorklet.addModule('/static/pcmWorkletProcessor.js');
    micWorkletNode = new AudioWorkletNode(audioContext, 'pcm-worklet-processor');

    micWorkletNode.port.onmessage = ({ data }) => {
      const incoming = new Int16Array(data);
      let read = 0;
      while (read < incoming.length) {
        initBatch();
        const toCopy = Math.min(
          incoming.length - read,
          BATCH_SAMPLES - batchOffset
        );
        batchInt16.set(
          incoming.subarray(read, read + toCopy),
          batchOffset
        );
        batchOffset += toCopy;
        read += toCopy;
        if (batchOffset === BATCH_SAMPLES) {
          flushBatch();
        }
      }
    };

    const source = audioContext.createMediaStreamSource(stream);
    source.connect(micWorkletNode);
    statusDiv.textContent = "正在录音...";
    
    // 隐藏提示（成功获取权限后）
    if (permissionHint) {
      permissionHint.style.display = 'none';
    }
  } catch (err) {
    console.error("麦克风访问错误:", err);
    
    let errorMsg = "麦克风访问被拒绝";
    
    if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
      errorMsg = "麦克风权限被拒绝。请点击地址栏的锁图标，允许麦克风访问，然后刷新页面。";
    } else if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
      errorMsg = "未找到麦克风设备。请确保已连接麦克风。";
    } else if (err.name === 'NotReadableError' || err.name === 'TrackStartError') {
      errorMsg = "无法访问麦克风。可能被其他应用占用，请关闭其他使用麦克风的应用。";
    } else if (err.name === 'OverconstrainedError' || err.name === 'ConstraintNotSatisfiedError') {
      errorMsg = "麦克风不支持请求的配置。尝试使用默认设置。";
    } else {
      errorMsg = `麦克风访问失败: ${err.name || '未知错误'}。详细信息请查看控制台。`;
    }
    
    statusDiv.textContent = errorMsg;
    
    // 显示错误提示
    if (permissionHint) {
      permissionHint.style.display = 'block';
      permissionHint.style.background = '#f8d7da';
      permissionHint.style.color = '#721c24';
      permissionHint.innerHTML = `<strong>错误：</strong>${errorMsg}`;
    }
    
    alert(errorMsg);
  }
}

async function setupTTSPlayback() {
  // 确保 AudioContext 已初始化
  if (!audioContext) {
    initAudioContext();
  }
  
  // 检查 AudioContext 状态
  if (audioContext.state === 'closed') {
    throw new Error("AudioContext 已关闭，无法设置 TTS 播放");
  }
  
  // 如果 AudioContext 被暂停，恢复它
  if (audioContext.state === 'suspended') {
    console.log("AudioContext 处于暂停状态，尝试恢复...");
    try {
      await audioContext.resume();
      console.log("AudioContext 已恢复，当前状态:", audioContext.state);
    } catch (err) {
      console.error("恢复 AudioContext 失败:", err);
      throw new Error("无法恢复 AudioContext: " + (err.message || err));
    }
  }
  
  // 检查是否支持 AudioWorklet
  if (!audioContext.audioWorklet) {
    throw new Error("浏览器不支持 AudioWorklet API。请使用 Chrome 66+、Edge 79+ 或 Firefox 76+");
  }
  
  console.log("开始加载 TTS AudioWorklet 模块...");
  try {
    await audioContext.audioWorklet.addModule('/static/ttsPlaybackProcessor.js');
    console.log("✓ TTS AudioWorklet 模块已加载");
  } catch (err) {
    console.error("✗ 加载 TTS AudioWorklet 模块失败:", err);
    console.error("错误详情:", err.message, err.stack);
    throw new Error("加载 TTS 播放处理器失败: " + (err.message || err));
  }
  
  console.log("开始创建 TTS AudioWorkletNode...");
  try {
    ttsWorkletNode = new AudioWorkletNode(
      audioContext,
      'tts-playback-processor'
    );
    console.log("✓ TTS AudioWorkletNode 已创建");
  } catch (err) {
    console.error("✗ 创建 TTS AudioWorkletNode 失败:", err);
    console.error("错误详情:", err.message, err.stack);
    throw new Error("创建 TTS 播放节点失败: " + (err.message || err));
  }

  ttsWorkletNode.port.onmessage = (event) => {
    const { type } = event.data;
    if (type === 'ttsPlaybackStarted') {
      if (!isTTSPlaying && socket && socket.readyState === WebSocket.OPEN) {
        isTTSPlaying = true;
        console.log("✓ TTS 播放开始");
        socket.send(JSON.stringify({ type: 'tts_start' }));
      }
    } else if (type === 'ttsPlaybackStopped') {
      if (isTTSPlaying && socket && socket.readyState === WebSocket.OPEN) {
        isTTSPlaying = false;
        console.log("✓ TTS 播放停止");
        socket.send(JSON.stringify({ type: 'tts_stop' }));
      }
    }
  };
  
  console.log("开始连接 TTS AudioWorkletNode 到音频输出...");
  try {
    ttsWorkletNode.connect(audioContext.destination);
    console.log("✓ TTS AudioWorkletNode 已连接到音频输出");
    console.log("TTS 播放器设置完成！AudioContext 状态:", audioContext.state);
  } catch (err) {
    console.error("✗ 连接 TTS 播放节点失败:", err);
    console.error("错误详情:", err.message, err.stack);
    throw new Error("连接 TTS 播放节点失败: " + (err.message || err));
  }
}

function cleanupAudio() {
  if (micWorkletNode) {
    micWorkletNode.disconnect();
    micWorkletNode = null;
  }
  if (ttsWorkletNode) {
    ttsWorkletNode.disconnect();
    ttsWorkletNode = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  if (mediaStream) {
    mediaStream.getAudioTracks().forEach(track => track.stop());
    mediaStream = null;
  }
}

function escapeHtml(str) {
  return (str ?? '')
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMessages() {
  messagesDiv.innerHTML = "";
  chatHistory.forEach(msg => {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${msg.role}`;
    bubble.textContent = msg.content;
    messagesDiv.appendChild(bubble);
  });
  if (typingUser) {
    const typing = document.createElement("div");
    typing.className = "bubble user typing";
    typing.innerHTML = escapeHtml(typingUser) + '<span style="opacity:.6;">✏️</span>';
    messagesDiv.appendChild(typing);
  }
  if (typingAssistant) {
    const typing = document.createElement("div");
    typing.className = "bubble assistant typing";
    typing.innerHTML = escapeHtml(typingAssistant) + '<span style="opacity:.6;">✏️</span>';
    messagesDiv.appendChild(typing);
  }
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

async function handleJSONMessage({ type, content }) {
  if (type === "partial_user_request") {
    typingUser = content?.trim() ? escapeHtml(content) : "";
    renderMessages();
    return;
  }
  if (type === "final_user_request") {
    if (content?.trim()) {
      // 检查是否已存在相同的用户消息（避免重复添加）
      const isDuplicate = chatHistory.some(
        msg => msg.role === "user" && msg.content === content && msg.type === "final"
      );
      if (!isDuplicate) {
        chatHistory.push({ role: "user", content, type: "final" });
      }
    }
    typingUser = "";
    // 用户新输入，立即清除旧任务的流式输出（如果有）
    typingAssistant = "";
    // 用户新输入，重置 ignoreIncomingTTS，允许新的 TTS 播放
    ignoreIncomingTTS = false;
    renderMessages();
    return;
  }
  if (type === "partial_assistant_answer") {
    // 流式输出：累积显示，不断更新
    typingAssistant = content?.trim() ? escapeHtml(content) : "";
    renderMessages();
    return;
  }
  if (type === "final_assistant_answer") {
    // 最终结果：添加到历史，清除流式显示
    if (content?.trim()) {
      chatHistory.push({ role: "assistant", content, type: "final" });
    }
    typingAssistant = "";
    // 新的助手回复完成，重置 ignoreIncomingTTS，允许新的 TTS 播放
    ignoreIncomingTTS = false;
    renderMessages();
    return;
  }
  if (type === "tts_chunk") {
    // 如果 ignoreIncomingTTS 为 true，但 TTS 已经停止播放（isTTSPlaying = false），
    // 说明这是新的对话轮次的音频，应该允许播放
    if (ignoreIncomingTTS && isTTSPlaying) {
      console.log("忽略 TTS 音频块（ignoreIncomingTTS=true 且正在播放）");
      return;
    }
    // 如果 TTS 已停止播放，重置 ignoreIncomingTTS，允许新的音频播放
    if (ignoreIncomingTTS && !isTTSPlaying) {
      console.log("检测到新的 TTS 音频（之前已停止），重置 ignoreIncomingTTS");
      ignoreIncomingTTS = false;
    }
    
    // 确保 AudioContext 已初始化
    if (!audioContext) {
      console.warn("收到 TTS 音频块，但 AudioContext 未初始化，尝试初始化...");
      try {
        initAudioContext();
        // 如果 TTS 播放器还没设置，尝试设置
        if (!ttsWorkletNode) {
          console.log("尝试自动设置 TTS 播放器...");
          setupTTSPlayback().catch(err => {
            console.error("自动设置 TTS 播放失败:", err);
          });
        }
      } catch (err) {
        console.error("初始化 AudioContext 失败:", err);
        return;
      }
    }
    
    // 确保 AudioContext 处于运行状态
    if (audioContext.state === 'suspended') {
      console.log("AudioContext 已暂停，尝试恢复...");
      audioContext.resume().catch(err => {
        console.error("恢复 AudioContext 失败:", err);
      });
    }
    
    // 如果 TTS 播放器还没设置，尝试设置
    if (!ttsWorkletNode) {
      console.warn("收到 TTS 音频块，但 ttsWorkletNode 未初始化，尝试设置...");
      setupTTSPlayback().catch(err => {
        console.error("设置 TTS 播放器失败:", err);
        return;
      });
      // 设置需要时间，等待一下
      await new Promise(resolve => setTimeout(resolve, 100));
      if (!ttsWorkletNode) {
        console.error("TTS 播放器设置失败，无法播放音频");
        return;
      }
    }
    
    try {
      const int16Data = base64ToInt16Array(content);
      if (ttsWorkletNode) {
        ttsWorkletNode.port.postMessage(int16Data);
        // 减少日志频率，每10个块记录一次
        if (!window.ttsChunkCount) window.ttsChunkCount = 0;
        window.ttsChunkCount++;
        if (window.ttsChunkCount % 10 === 0) {
          console.log("TTS 音频块已发送到播放器:", window.ttsChunkCount, "个块, 当前块大小:", int16Data.length, "个样本");
        }
      } else {
        console.error("收到 TTS 音频块，但 ttsWorkletNode 仍未初始化");
      }
    } catch (err) {
      console.error("处理 TTS 音频块时出错:", err, err.stack);
    }
    return;
  }
  if (type === "tts_interruption") {
    if (ttsWorkletNode) {
      ttsWorkletNode.port.postMessage({ type: "clear" });
    }
    isTTSPlaying = false;
    ignoreIncomingTTS = false;
    return;
  }
  if (type === "stop_tts") {
    if (ttsWorkletNode) {
      ttsWorkletNode.port.postMessage({ type: "clear" });
    }
    isTTSPlaying = false;
    ignoreIncomingTTS = true;
    console.log("TTS 播放停止（中断）");
    socket.send(JSON.stringify({ type: 'tts_stop' }));
    return;
  }
}

// UI 控制
document.getElementById("clearBtn").onclick = () => {
  chatHistory = [];
  typingUser = typingAssistant = "";
  renderMessages();
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'clear_history' }));
  }
};

// 检测浏览器信息
function getBrowserInfo() {
  const ua = navigator.userAgent;
  let browser = "未知";
  let version = "";
  
  if (ua.includes("Edg/")) {
    browser = "Edge";
    const match = ua.match(/Edg\/(\d+)/);
    version = match ? match[1] : "";
  } else if (ua.includes("Chrome/")) {
    browser = "Chrome";
    const match = ua.match(/Chrome\/(\d+)/);
    version = match ? match[1] : "";
  } else if (ua.includes("Firefox/")) {
    browser = "Firefox";
    const match = ua.match(/Firefox\/(\d+)/);
    version = match ? match[1] : "";
  } else if (ua.includes("Safari/") && !ua.includes("Chrome")) {
    browser = "Safari";
    const match = ua.match(/Version\/(\d+)/);
    version = match ? match[1] : "";
  }
  
  return { browser, version, userAgent: ua };
}

document.getElementById("startBtn").onclick = async () => {
  if (socket && socket.readyState === WebSocket.OPEN) {
    statusDiv.textContent = "已连接";
    return;
  }
  
  // 先检查并请求麦克风权限（在连接 WebSocket 之前）
  try {
    statusDiv.textContent = "正在检查麦克风权限...";
    
    // 获取浏览器信息
    const browserInfo = getBrowserInfo();
    console.log("浏览器信息:", browserInfo);
    
    // 检查协议
    const isSecure = window.location.protocol === 'https:' || 
                     window.location.hostname === 'localhost' || 
                     window.location.hostname === '127.0.0.1' ||
                     window.location.hostname === '0.0.0.0';
    
    // 检查是否支持 getUserMedia
    if (!navigator.mediaDevices) {
      let errorMsg = `检测到 ${browserInfo.browser} 浏览器`;
      if (browserInfo.version) {
        errorMsg += ` (版本 ${browserInfo.version})`;
      }
      errorMsg += "\n\n";
      
      if (!isSecure && window.location.protocol === 'http:') {
        errorMsg += "问题：当前使用 HTTP 协议，某些浏览器要求 HTTPS 或 localhost。\n";
        errorMsg += "解决方案：请使用 http://localhost:8000 或 http://127.0.0.1:8000 访问。\n\n";
      }
      
      errorMsg += "可能的原因：\n";
      errorMsg += "1. 浏览器版本过旧（Edge 需要 79+，Chrome 需要 60+）\n";
      errorMsg += "2. 浏览器设置中禁用了媒体设备访问\n";
      errorMsg += "3. 需要 HTTPS 或 localhost 环境\n\n";
      errorMsg += "提示：即使麦克风不可用，您仍可以使用下方的文本输入框与系统交流。";
      
      statusDiv.textContent = "浏览器不支持麦克风访问（可使用文本输入）";
      alert(errorMsg);
      
      // 即使不支持麦克风，也允许连接 WebSocket（可以使用文本输入）
      statusDiv.textContent = "正在初始化连接（仅文本模式）...";
    } else if (!navigator.mediaDevices.getUserMedia) {
      // 检查是否有降级方案
      const getUserMedia = navigator.mediaDevices.getUserMedia || 
                          navigator.getUserMedia || 
                          navigator.webkitGetUserMedia || 
                          navigator.mozGetUserMedia;
      
      if (!getUserMedia) {
        let errorMsg = `检测到 ${browserInfo.browser} 浏览器，但不支持 getUserMedia API。\n\n`;
        errorMsg += "提示：您可以使用下方的文本输入框与系统交流。";
        statusDiv.textContent = "浏览器不支持麦克风访问（可使用文本输入）";
        alert(errorMsg);
        statusDiv.textContent = "正在初始化连接（仅文本模式）...";
      } else {
        // 有降级方案，继续尝试
        statusDiv.textContent = "正在请求麦克风权限...";
        const testStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        testStream.getTracks().forEach(track => track.stop());
        statusDiv.textContent = "正在初始化连接...";
      }
    } else {
      // 检查协议警告
      if (!isSecure) {
        console.warn("警告：当前不是 HTTPS 或 localhost，某些浏览器可能无法访问麦克风");
        statusDiv.textContent = "警告：建议使用 localhost 访问以获取更好的兼容性";
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
      
      // 先尝试获取麦克风权限（不实际使用，只是检查）
      statusDiv.textContent = "正在请求麦克风权限...";
      const testStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // 立即停止测试流
      testStream.getTracks().forEach(track => track.stop());
      
      statusDiv.textContent = "正在初始化连接...";
    }
  } catch (err) {
    // 如果权限检查失败，提前告知用户
    const browserInfo = getBrowserInfo();
    let errorMsg = "无法访问麦克风\n\n";
    errorMsg += `浏览器：${browserInfo.browser}`;
    if (browserInfo.version) {
      errorMsg += ` ${browserInfo.version}`;
    }
    errorMsg += "\n\n";
    
    if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
      errorMsg += "原因：麦克风权限被拒绝\n\n";
      errorMsg += "解决方案：\n";
      errorMsg += "1. 点击地址栏左侧的锁图标或信息图标\n";
      errorMsg += "2. 找到'麦克风'或'使用您的麦克风'选项\n";
      errorMsg += "3. 选择'允许'\n";
      errorMsg += "4. 刷新页面后重试\n\n";
    } else if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
      errorMsg += "原因：未找到麦克风设备\n\n";
      errorMsg += "解决方案：\n";
      errorMsg += "1. 确保已连接麦克风设备\n";
      errorMsg += "2. 检查系统设置中的麦克风权限\n\n";
    } else if (err.name === 'NotReadableError' || err.name === 'TrackStartError') {
      errorMsg += "原因：无法访问麦克风（可能被其他应用占用）\n\n";
      errorMsg += "解决方案：\n";
      errorMsg += "1. 关闭其他使用麦克风的应用\n";
      errorMsg += "2. 检查系统设置中的麦克风权限\n\n";
    } else {
      errorMsg += `原因：${err.name || '未知错误'}\n`;
      errorMsg += `详细信息：${err.message || '无'}\n\n`;
    }
    
    errorMsg += "提示：即使麦克风不可用，您仍可以使用下方的文本输入框与系统交流。";
    
    statusDiv.textContent = "麦克风访问失败（可使用文本输入）";
    alert(errorMsg);
    
    // 即使麦克风失败，也允许连接 WebSocket（可以使用文本输入）
    statusDiv.textContent = "正在初始化连接（仅文本模式）...";
  }

  const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${wsProto}//${location.host}/ws`);

  socket.onopen = async () => {
    statusDiv.textContent = "已连接，正在初始化音频...";
    
    // 首先初始化 AudioContext（即使没有麦克风也需要，用于 TTS 播放）
    try {
      initAudioContext();
      console.log("✓ AudioContext 已初始化，采样率:", audioContext.sampleRate, "状态:", audioContext.state);
    } catch (err) {
      console.error("✗ 初始化 AudioContext 失败:", err);
      statusDiv.textContent = "已连接（音频初始化失败，但可以使用文本输入）";
    }
    
    // 尝试激活麦克风（如果支持）
    let micEnabled = false;
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      try {
        await startRawPcmCapture();
        micEnabled = true;
        console.log("✓ 麦克风已激活");
      } catch (micErr) {
        console.warn("✗ 麦克风激活失败:", micErr);
        // 麦克风失败不影响 TTS 播放
      }
    } else {
      console.warn("浏览器不支持麦克风，跳过音频采集");
    }
    
    // 始终尝试设置 TTS 播放（即使没有麦克风）
    try {
      console.log("开始设置 TTS 播放器...");
      await setupTTSPlayback();
      console.log("✓ TTS 播放器设置完成");
      
      if (micEnabled) {
        statusDiv.textContent = "已连接，可以开始对话（语音或文字）";
      } else {
        statusDiv.textContent = "已连接，可以使用文字输入（TTS 播放已就绪）";
      }
    } catch (ttsErr) {
      console.error("✗ TTS 播放设置失败:", ttsErr);
      console.error("错误堆栈:", ttsErr.stack);
      statusDiv.textContent = "已连接（TTS 播放设置失败: " + (ttsErr.message || ttsErr) + "）";
      // 显示详细错误信息
      alert("TTS 播放设置失败：\n" + (ttsErr.message || ttsErr) + "\n\n可能的原因：\n1. 浏览器不支持 AudioWorklet（需要 Chrome 66+、Edge 79+ 或 Firefox 76+）\n2. AudioContext 初始化失败\n3. 音频文件路径错误\n\n请查看浏览器控制台获取更多信息。");
    }
  };

  socket.onmessage = async (evt) => {
    if (typeof evt.data === "string") {
      try {
        const msg = JSON.parse(evt.data);
        await handleJSONMessage(msg);
      } catch (e) {
        console.error("解析消息错误:", e);
      }
    }
  };

  socket.onclose = () => {
    statusDiv.textContent = "连接已关闭";
    flushRemainder();
    cleanupAudio();
  };

  socket.onerror = (err) => {
    statusDiv.textContent = "连接错误";
    cleanupAudio();
    console.error(err);
  };
};

document.getElementById("stopBtn").onclick = () => {
  if (socket && socket.readyState === WebSocket.OPEN) {
    flushRemainder();
    socket.close();
  }
  cleanupAudio();
  statusDiv.textContent = "已停止";
};

document.getElementById("copyBtn").onclick = () => {
  const text = chatHistory
    .map(msg => `${msg.role === 'user' ? '用户' : '助手'}: ${msg.content}`)
    .join('\n');
  
  navigator.clipboard.writeText(text)
    .then(() => {
      statusDiv.textContent = "已复制到剪贴板";
      setTimeout(() => {
        if (socket && socket.readyState === WebSocket.OPEN) {
          statusDiv.textContent = "已连接";
        } else {
          statusDiv.textContent = "未连接";
        }
      }, 2000);
    })
    .catch(err => console.error("复制失败:", err));
};

// 文本输入功能
function sendTextMessage() {
  const text = textInput.value.trim();
  if (!text) {
    return;
  }
  
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    statusDiv.textContent = "未连接，请先点击'开始'按钮";
    return;
  }
  
  // 发送文本消息（不立即显示，等待后端验证和处理）
  socket.send(JSON.stringify({
    type: 'text_input',
    content: text
  }));
  
  // 清空输入框（但不在对话框中显示，等待后端确认）
  textInput.value = '';
  // 重置文本框高度
  textInput.style.height = 'auto';
  
  // 更新状态
  statusDiv.textContent = "已发送，等待确认...";
}

// 自动调整文本框高度
function autoResizeTextarea() {
  textInput.style.height = 'auto';
  textInput.style.height = Math.min(textInput.scrollHeight, 120) + 'px';
}

// 文本输入框事件
if (textInput) {
  // 回车发送，Shift+Enter 换行
  textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendTextMessage();
    }
  });
  
  // 自动调整高度
  textInput.addEventListener('input', autoResizeTextarea);
}

// 发送按钮事件
if (sendTextBtn) {
  sendTextBtn.addEventListener('click', sendTextMessage);
}

// 首次渲染
renderMessages();

// 页面加载时检查浏览器兼容性和权限状态
window.addEventListener('load', async () => {
  // 输出诊断信息到控制台
  const browserInfo = getBrowserInfo();
  console.log("=== 浏览器诊断信息 ===");
  console.log("浏览器:", browserInfo.browser, browserInfo.version);
  console.log("User Agent:", browserInfo.userAgent);
  console.log("协议:", window.location.protocol);
  console.log("主机名:", window.location.hostname);
  console.log("navigator.mediaDevices 存在:", !!navigator.mediaDevices);
  console.log("navigator.mediaDevices.getUserMedia 存在:", !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia));
  console.log("AudioContext 支持:", typeof AudioContext !== 'undefined');
  console.log("AudioWorklet 支持:", typeof AudioWorkletNode !== 'undefined');
  console.log("====================");
  
  // 检查浏览器版本（Edge 需要 79+）
  if (browserInfo.browser === "Edge" && browserInfo.version) {
    const versionNum = parseInt(browserInfo.version);
    if (versionNum < 79) {
      const oldEdgeMsg = "检测到旧版 Edge 浏览器（版本 " + browserInfo.version + "）。\n\n";
      const newEdgeMsg = "建议升级到基于 Chromium 的 Edge（版本 79+）以获得完整支持。\n\n";
      const fallbackMsg = "当前可以使用文本输入功能与系统交流。";
      console.warn(oldEdgeMsg + newEdgeMsg + fallbackMsg);
      
      if (permissionHint) {
        permissionHint.style.display = 'block';
        permissionHint.style.background = '#fff3cd';
        permissionHint.style.color = '#856404';
        permissionHint.innerHTML = '<strong>提示：</strong>检测到旧版 Edge 浏览器。建议升级到 Edge 79+ 以获得麦克风支持，或使用文本输入功能。';
      }
    }
  }
  
  // 检查协议
  const isSecure = window.location.protocol === 'https:' || 
                   window.location.hostname === 'localhost' || 
                   window.location.hostname === '127.0.0.1' ||
                   window.location.hostname === '0.0.0.0';
  
  if (!isSecure && window.location.protocol === 'http:') {
    console.warn("警告：当前使用 HTTP 协议，某些浏览器可能无法访问麦克风。建议使用 localhost 访问。");
    if (permissionHint && !permissionHint.style.display || permissionHint.style.display === 'none') {
      permissionHint.style.display = 'block';
      permissionHint.style.background = '#fff3cd';
      permissionHint.style.color = '#856404';
      permissionHint.innerHTML = '<strong>提示：</strong>建议使用 <code>http://localhost:8000</code> 访问以获得更好的兼容性。';
    }
  }
  
  // 检查权限状态
  try {
    const permissionState = await checkMicrophonePermission();
    console.log("麦克风权限状态:", permissionState);
    if (permissionState === 'prompt' && permissionHint && (!permissionHint.style.display || permissionHint.style.display === 'none')) {
      permissionHint.style.display = 'block';
    }
  } catch (e) {
    console.log("无法检查权限状态（某些浏览器不支持）:", e);
  }
});
