export const ROBOT36_WIDTH = 320;
export const ROBOT36_HEIGHT = 240;
export const ROBOT36_SAMPLE_RATE = 48000;
export const ROBOT36_DURATION = 36.91;

const TAU = Math.PI * 2;
const VIS_CODE = 0x08;

function clampByte(value) {
  return Math.max(0, Math.min(255, Math.round(value)));
}

function rgbToYCbCr(r, g, b) {
  return [
    clampByte(0.299 * r + 0.587 * g + 0.114 * b),
    clampByte(-0.168736 * r - 0.331264 * g + 0.5 * b + 128),
    clampByte(0.5 * r - 0.418688 * g - 0.081312 * b + 128),
  ];
}

function pixelFrequency(value) {
  return 1500 + (800 * value) / 255;
}

export async function encodeRobot36(rgba, onProgress = () => {}) {
  if (!rgba || rgba.length !== ROBOT36_WIDTH * ROBOT36_HEIGHT * 4) {
    throw new Error("图片必须是 320×240 RGBA 像素数据");
  }
  const samples = new Int16Array(Math.ceil(ROBOT36_DURATION * ROBOT36_SAMPLE_RATE) + 64);
  let writeIndex = 0;
  let sampleRemainder = 0;
  let phase = 0;

  const tone = (frequency, milliseconds) => {
    sampleRemainder += (ROBOT36_SAMPLE_RATE * milliseconds) / 1000;
    const count = Math.floor(sampleRemainder);
    sampleRemainder -= count;
    const phaseStep = (TAU * frequency) / ROBOT36_SAMPLE_RATE;
    for (let index = 0; index < count; index += 1) {
      samples[writeIndex++] = Math.round(Math.sin(phase) * 32767);
      phase += phaseStep;
      if (phase >= TAU) phase -= TAU;
    }
  };

  tone(1900, 300);
  tone(1200, 10);
  tone(1900, 300);
  tone(1200, 30);
  let vis = VIS_CODE;
  let ones = 0;
  for (let bitIndex = 0; bitIndex < 7; bitIndex += 1) {
    const bit = vis & 1;
    vis >>= 1;
    ones += bit;
    tone(bit ? 1100 : 1300, 30);
  }
  tone(ones % 2 ? 1100 : 1300, 30);
  tone(1200, 30);

  const yLine = new Uint8Array(ROBOT36_WIDTH);
  const chromaLine = new Uint8Array(ROBOT36_WIDTH);
  for (let y = 0; y < ROBOT36_HEIGHT; y += 1) {
    const chromaChannel = y % 2 === 0 ? 2 : 1;
    for (let x = 0; x < ROBOT36_WIDTH; x += 1) {
      const offset = (y * ROBOT36_WIDTH + x) * 4;
      const ycbcr = rgbToYCbCr(rgba[offset], rgba[offset + 1], rgba[offset + 2]);
      yLine[x] = ycbcr[0];
      chromaLine[x] = ycbcr[chromaChannel];
    }
    tone(1200, 9);
    tone(1500, 3);
    for (let x = 0; x < ROBOT36_WIDTH; x += 1) tone(pixelFrequency(yLine[x]), 88 / ROBOT36_WIDTH);
    tone(y % 2 === 0 ? 1500 : 2300, 4.5);
    tone(1900, 1.5);
    for (let x = 0; x < ROBOT36_WIDTH; x += 1) tone(pixelFrequency(chromaLine[x]), 44 / ROBOT36_WIDTH);
    if (y % 8 === 7) {
      onProgress((y + 1) / ROBOT36_HEIGHT);
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }
  onProgress(1);
  return samples.slice(0, writeIndex);
}

export function createMono16Wav(samples, sampleRate = ROBOT36_SAMPLE_RATE) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, value) => [...value].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let index = 0; index < samples.length; index += 1) view.setInt16(44 + index * 2, samples[index], true);
  return new Blob([buffer], { type: "audio/wav" });
}
