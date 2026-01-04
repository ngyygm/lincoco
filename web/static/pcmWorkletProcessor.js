// pcmWorkletProcessor.js
// AudioWorklet 处理器：将麦克风输入的 Float32 音频转换为 Int16 PCM 格式
class PCMWorkletProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const in32 = inputs[0][0];
    if (in32) {
      // 将 Float32 转换为 Int16
      const int16 = new Int16Array(in32.length);
      for (let i = 0; i < in32.length; i++) {
        let s = in32[i];
        // 限制范围在 [-1, 1]
        s = s < -1 ? -1 : s > 1 ? 1 : s;
        // 转换为 Int16
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      // 发送原始 ArrayBuffer（可转移）
      this.port.postMessage(int16.buffer, [int16.buffer]);
    }
    return true;
  }
}

registerProcessor('pcm-worklet-processor', PCMWorkletProcessor);
