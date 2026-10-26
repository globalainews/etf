function drawLine(canvasId, points, key, color, unit, selectedIndex = -1) {
  const ctx = wx.createCanvasContext(canvasId);
  const width = 335; const height = 150; const left = 42; const right = 12; const top = 15; const bottom = 28;
  ctx.setFillStyle('#ffffff'); ctx.fillRect(0, 0, width, height);
  const valid = points.map((point, index) => ({ index, value: point[key] })).filter(item => item.value !== null && item.value !== undefined);
  if (valid.length < 2) { ctx.setFillStyle('#7b8793'); ctx.setFontSize(13); ctx.fillText('有效数据不足，无法绘制趋势', 74, 78); ctx.draw(); return; }
  const values = valid.map(item => Number(item.value)); let min = Math.min(...values); let max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  ctx.setStrokeStyle('#e5ebef'); ctx.setLineWidth(1);
  [0, .5, 1].forEach(rate => { const y = top + (height - top - bottom) * rate; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke(); });
  ctx.setStrokeStyle(color); ctx.setLineWidth(2); ctx.beginPath(); let started = false;
  points.forEach((point, index) => {
    if (point[key] === null || point[key] === undefined) { started = false; return; }
    const x = left + (width - left - right) * (index / Math.max(points.length - 1, 1));
    const y = top + (max - Number(point[key])) / (max - min) * (height - top - bottom);
    if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
  }); ctx.stroke();
  ctx.setFillStyle('#758391'); ctx.setFontSize(10); ctx.fillText(String(max.toFixed(2)) + unit, 2, top + 4); ctx.fillText(String(min.toFixed(2)) + unit, 2, height - bottom + 4);
  ctx.fillText(points[0].date.slice(5), left, height - 7); ctx.fillText(points[points.length - 1].date.slice(5), width - 44, height - 7);
  if (selectedIndex >= 0 && points[selectedIndex] && points[selectedIndex][key] !== null) {
    const x = left + (width - left - right) * (selectedIndex / Math.max(points.length - 1, 1));
    const y = top + (max - Number(points[selectedIndex][key])) / (max - min) * (height - top - bottom);
    // 选中点用空心圆标识；不画竖线，避免与价格/份额走势误读为一次急跌。
    ctx.setFillStyle('#ffffff'); ctx.setStrokeStyle(color); ctx.setLineWidth(3);
    ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }
  ctx.draw();
}

module.exports = { drawLine };

