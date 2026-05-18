/**
 * Tree View — 层级零件树，支持点击选择 + 颜色色块 + 阶段徽章。
 */

export class TreeView {

  /**
   * @param {HTMLElement} container
   * @param {object} callbacks - { onSelect(id), onColorChange(id, color) }
   */
  constructor(container, callbacks = {}) {
    this.container = container;
    this.callbacks = callbacks;
    this.parts = [];
    this.selected = null;
    this._colorMap = {};   // partId -> '#rrggbb'
  }

  /** 获取当前颜色映射 */
  get colors() { return this._colorMap; }

  /**
   * @param {Array} parts - from assembly.json
   * @param {Array} stages - from assembly.json
   */
  build(parts, stages = []) {
    this.container.innerHTML = '';
    this.parts = parts;

    const stageMap = {};
    if (stages) {
      for (const s of stages) {
        for (const pid of (s.parts || [])) {
          stageMap[pid] = s.stage;
        }
      }
    }

    if (!parts.length) {
      const el = document.createElement('div');
      el.className = 'tree-item';
      el.textContent = '(空)';
      this.container.appendChild(el);
      return;
    }

    for (const part of parts) {
      this._renderItem(part, stageMap);
    }
  }

  _renderItem(part, stageMap) {
    const el = document.createElement('div');
    el.className = 'tree-item';
    el.style.paddingLeft = '12px';
    el.dataset.partId = part.id;

    // Icon
    const icon = document.createElement('span');
    icon.className = 'icon';
    icon.textContent = part.isFastener ? '🔩' : '🔧';
    el.appendChild(icon);

    // Name
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = part.name || part.id;
    name.title = part.name || part.id;
    el.appendChild(name);

    // Stage badge
    const stage = part.disassemblyStage || stageMap[part.id];
    if (stage) {
      const badge = document.createElement('span');
      badge.className = 'badge stage';
      badge.textContent = 'S' + stage;
      el.appendChild(badge);
    }

    // Fastener badge
    if (part.isFastener) {
      const badge = document.createElement('span');
      badge.className = 'badge';
      badge.textContent = '紧固件';
      el.appendChild(badge);
    }

    // Color swatch
    const swatch = document.createElement('span');
    swatch.style.cssText = 'width:14px;height:14px;border-radius:3px;margin-left:6px;flex-shrink:0;cursor:pointer;border:1px solid #555';
    const color = this._colorMap[part.id] || '#808080';
    swatch.style.backgroundColor = color;
    swatch.title = '点击修改颜色';

    swatch.addEventListener('click', (e) => {
      e.stopPropagation();
      this._showColorPicker(swatch, part.id);
    });
    el.appendChild(swatch);

    // Click handler
    el.addEventListener('click', () => {
      this._select(el, part);
    });

    this.container.appendChild(el);
  }

  _select(el, part) {
    if (this.selected) {
      this.selected.classList.remove('selected');
    }
    this.selected = el;
    el.classList.add('selected');

    if (this.callbacks.onSelect) {
      this.callbacks.onSelect(part.id);
    }
  }

  _showColorPicker(swatch, partId) {
    const input = document.createElement('input');
    input.type = 'color';
    input.value = this._colorMap[partId] || '#808080';
    input.style.position = 'fixed';
    input.style.opacity = '0';
    document.body.appendChild(input);
    input.click();

    input.addEventListener('input', () => {
      const v = input.value;
      swatch.style.backgroundColor = v;
      this._colorMap[partId] = v;
      if (this.callbacks.onColorChange) {
        this.callbacks.onColorChange(partId, v);
      }
    });

    input.addEventListener('change', () => {
      document.body.removeChild(input);
    });

    // Handle cancel (blur without change)
    input.addEventListener('blur', () => {
      setTimeout(() => {
        if (input.parentNode) document.body.removeChild(input);
      }, 200);
    });
  }

  getSelected() {
    return this.selected ? this.selected.dataset.partId : null;
  }
}
