/**
 * Export — PNG screenshot and batch export for disassembly stages.
 *
 * Uses Three.js renderer's toDataURL for high-quality image capture.
 */

export class ExportManager {

  /**
   * @param {THREE.WebGLRenderer} renderer
   */
  constructor(renderer) {
    this.renderer = renderer;
  }

  /**
   * Capture current viewport as PNG data URL.
   *
   * @returns {string} PNG data URL
   */
  captureCurrent() {
    // Ensure a fresh render before capture
    return this.renderer.domElement.toDataURL('image/png');
  }

  /**
   * Trigger a browser download of the current screenshot.
   *
   * @param {string} [filename='screenshot.png']
   */
  downloadCurrent(filename = 'screenshot.png') {
    const dataUrl = this.captureCurrent();
    this._download(dataUrl, filename);
  }

  /**
   * Capture screenshots for all disassembly stages.
   *
   * @param {number} numStages - total number of stages
   * @param {THREE.Vector3} [center] - focus center
   * @param {function} onStage - called to trigger stage N explosion
   * @returns {Promise<Array>} Array of { stage, label, dataUrl }
   */
  async captureAllStages(numStages, center, onStage) {
    const captures = [];

    for (let stage = 1; stage <= numStages; stage++) {
      // Trigger the explosion to this stage
      if (onStage) {
        await onStage(stage);
      }

      // Multiple viewpoints per stage
      const views = [
        { name: 'iso', offset: [1, 0.7, 1] },
        { name: 'front', offset: [0, 0.5, 1] },
        { name: 'side', offset: [1, 0.5, 0] },
        { name: 'top', offset: [0, 1, 0.01] },
      ];

      for (const view of views) {
        // Wait for render
        await new Promise((r) => requestAnimationFrame(r));

        const dataUrl = this.captureCurrent();
        captures.push({
          stage,
          label: 'stage_' + stage + '_' + view.name,
          dataUrl,
        });

        // Small delay between viewpoints
        await new Promise((r) => setTimeout(r, 150));
      }
    }

    return captures;
  }

  /**
   * Download all captures as individual PNG files in sequence.
   */
  async downloadAllStages(numStages, center, onStage) {
    const captures = await this.captureAllStages(numStages, center, onStage);

    for (let i = 0; i < captures.length; i++) {
      const c = captures[i];
      this._download(c.dataUrl, c.label + '.png');
      await new Promise((r) => setTimeout(r, 100));
    }

    return captures.length;
  }

  _download(dataUrl, filename) {
    const link = document.createElement('a');
    link.download = filename;
    link.href = dataUrl;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  _downloadText(content, filename) {
    const blob = new Blob([content], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.download = filename;
    link.href = url;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  exportSVG(canvasDataUrl, annotations, viewportW, viewportH, filename = 'export.svg') {
    const lines = [];
    lines.push('<?xml version="1.0" encoding="utf-8"?>');
    lines.push('<svg version="1.1" xmlns="http://www.w3.org/2000/svg"');
    lines.push('     width="' + viewportW + '" height="' + viewportH + '"');
    lines.push('     viewBox="0 0 ' + viewportW + ' ' + viewportH + '">');
    lines.push('');
    lines.push('  <g id="background">');
    lines.push('    <image href="' + canvasDataUrl + '" x="0" y="0" width="' + viewportW + '" height="' + viewportH + '" />');
    lines.push('  </g>');
    lines.push('');
    lines.push('  <g id="annotations">');
    if (annotations && annotations.length > 0) {
      for (let i = 0; i < annotations.length; i++) {
        const a = annotations[i];
        const cx = a.circleX || a.sx;
        const cy = a.circleY || a.sy;
        const tx = a.targetX || a.sx;
        const ty = a.targetY || a.sy;
        const num = a.number || (i + 1);
        const r = 12;
        lines.push('    <g id="item' + (i + 1) + '">');
        lines.push('      <line x1="' + cx + '" y1="' + cy + '" x2="' + tx + '" y2="' + ty + '" stroke="#222" stroke-width="1.5" />');
        lines.push('      <circle cx="' + cx + '" cy="' + cy + '" r="' + (r + 2) + '" fill="#fff" stroke="#000" stroke-width="2" />');
        lines.push('      <text x="' + cx + '" y="' + cy + '" text-anchor="middle" dominant-baseline="central" font-size="12" font-family="sans-serif" font-weight="bold" fill="#000">' + num + '</text>');
        lines.push('    </g>');
      }
    }
    lines.push('  </g>');
    lines.push('</svg>');
    const svg = lines.join('\n');
    this._downloadText(svg, filename);
    return svg;
  }
}
