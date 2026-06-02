/**
 * Tree View — 多级可折叠零件树，支持选择、固定参照、颜色修改、层级勾选。
 */

export class TreeView {

  constructor(container, callbacks = {}) {
    this.container = container;
    this.callbacks = callbacks;
    this.hierarchy = [];
    this.partsMap = {};
    this.stagesMap = {};
    this.selectedEl = null;
    this.selectedNodeId = null;
    this.selectedPartIds = [];
    this._colorMap = {};
    this._fixedPartIds = new Set();
    this._collapsed = new Set();
    this._lastCheckedNodeId = null;
    this._checkedPartIds = new Set();
    this._hiddenPartIds = new Set();
    this._nodeIdToPartIds = new Map();
  }

  build(hierarchy, parts, stages = []) {
    this.container.innerHTML = '';
    this.hierarchy = hierarchy || [];

    this.partsMap = {};
    if (parts) {
      for (const p of parts) {
        this.partsMap[p.id] = p;
      }
    }

    this.stagesMap = {};
    if (stages) {
      for (const s of stages) {
        for (const pid of (s.parts || [])) {
          this.stagesMap[pid] = s.stage;
        }
      }
    }

    this._buildNodeIndex();

    if (!this.hierarchy.length) {
      const el = document.createElement('div');
      el.className = 'tree-node';
      el.style.paddingLeft = '12px';
      el.textContent = '(空)';
      this.container.appendChild(el);
      return;
    }

    for (const node of this.hierarchy) {
      this._renderNode(node, this.container, 0);
    }
    // Sync checkbox tri-state on initial render
    this._refreshCheckboxes();
  }

  _buildNodeIndex() {
    this._nodeIdToPartIds = new Map();
    const visit = (node) => {
      const ids = new Set();
      this._collectPartIds(node, ids);
      this._nodeIdToPartIds.set(node.id, ids);
      for (const c of node.children || []) visit(c);
    };
    for (const r of this.hierarchy) visit(r);
  }

  _renderNode(node, parentEl, depth) {
    const hasChildren = node.children && node.children.length > 0;
    const isLeaf = !hasChildren && node.partIds && node.partIds.length === 1;
    const isFixed = this._isNodeFixed(node);
    const lookupId = (node.partIds && node.partIds.length > 0) ? node.partIds[0] : node.id;
    const part = this.partsMap[lookupId] || this.partsMap[node.id];
    const stage = part ? (part.disassemblyStage || this.stagesMap[node.id]) : this.stagesMap[node.id];
    const isFastener = part ? part.isFastener : false;
    const nodePartIds = node.partIds || [];

    const row = document.createElement('div');
    row.className = 'tree-node';
    row.style.paddingLeft = (12 + depth * 16) + 'px';
    row.dataset.nodeId = node.id;

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'tree-check';
    // Tri-state will be set by _refreshCheckboxes after full render
    checkbox.addEventListener('click', (e) => {
      e.stopPropagation();
      this._toggleCheck(node);
    });
    row.appendChild(checkbox);

    const arrow = document.createElement('span');
    arrow.className = 'arrow' + (hasChildren ? '' : ' leaf');
    if (hasChildren) {
      arrow.textContent = this._collapsed.has(node.id) ? '▶' : '▼';
    }
    row.appendChild(arrow);

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = node.name || node.id;
    name.title = node.name || node.id;
    row.appendChild(name);

    const eye = document.createElement('span');
    eye.className = 'eye-icon';
    const isHidden = this._isNodeHidden(node);
    eye.textContent = isHidden ? '◌' : '●';
    eye.title = isHidden ? '点击显示' : '点击隐藏';
    eye.addEventListener('click', (e) => {
      e.stopPropagation();
      this._toggleVisibility(node, eye);
    });
    row.appendChild(eye);

    const count = nodePartIds.length;
    if (!isLeaf && count > 0) {
      const countBadge = document.createElement('span');
      countBadge.className = 'badge';
      countBadge.textContent = count;
      row.appendChild(countBadge);
    }

    if (stage && stage > 0) {
      const stageBadge = document.createElement('span');
      stageBadge.className = 'badge stage';
      stageBadge.textContent = 'S' + stage;
      row.appendChild(stageBadge);
    }

    if (isFastener) {
      const fb = document.createElement('span');
      fb.className = 'badge';
      fb.textContent = '紧固件';
      row.appendChild(fb);
    }

    if (isFixed) {
      const fxb = document.createElement('span');
      fxb.className = 'badge fixed';
      fxb.textContent = '固定';
      row.appendChild(fxb);
    }

    if (node.partIds && node.partIds.length > 0) {
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      const storedColor = this._colorMap[lookupId] || this._colorMap[node.partIds[0]];
      const color = storedColor || (part && part.color
        ? '#' + ((1 << 24) + (Math.round(part.color[0]*255) << 16) + (Math.round(part.color[1]*255) << 8) + Math.round(part.color[2]*255)).toString(16).slice(1)
        : '#0080c0');
      swatch.style.backgroundColor = color;
      swatch.title = '点击修改颜色' + (node.partIds.length > 1 && !isLeaf ? ' (含' + node.partIds.length + '子件)' : '');
      swatch.addEventListener('click', (e) => {
        e.stopPropagation();
        this._showColorPicker(swatch, node);
      });
      row.appendChild(swatch);
    }

    const childrenEl = document.createElement('div');

    row.addEventListener('click', (e) => {
      if (e.target.classList.contains('arrow') && hasChildren) {
        this._toggleCollapse(node.id, childrenEl, arrow);
        return;
      }
      if (e.target.tagName === 'INPUT') return;
      if (e.target.classList.contains('eye-icon')) return;
      this._select(row, node);
    });

    parentEl.appendChild(row);

    childrenEl.className = 'tree-children' + (this._collapsed.has(node.id) ? ' collapsed' : '');
    if (hasChildren) {
      for (const child of node.children) {
        this._renderNode(child, childrenEl, depth + 1);
      }
    }
    parentEl.appendChild(childrenEl);
  }

  _toggleCheck(node) {
    const nodePartIds = this._nodeIdToPartIds.get(node.id)
      || (() => { const s = new Set(); this._collectPartIds(node, s); return s; })();

    // Tri-state semantics: if all children currently checked, uncheck them all;
    // otherwise, check them all (accumulating with existing selections).
    let allChecked = nodePartIds.size > 0;
    for (const pid of nodePartIds) {
      if (!this._checkedPartIds.has(pid)) { allChecked = false; break; }
    }

    if (allChecked) {
      for (const pid of nodePartIds) this._checkedPartIds.delete(pid);
      this._lastCheckedNodeId = null;
    } else {
      for (const pid of nodePartIds) this._checkedPartIds.add(pid);
      this._lastCheckedNodeId = node.id;
    }
    this._refreshCheckboxes();
    if (this.callbacks.onCheckChange) {
      this.callbacks.onCheckChange(this._lastCheckedNodeId, this._checkedPartIds);
    }
  }

  _collectPartIds(node, outSet) {
    if (node.partIds) {
      for (const pid of node.partIds) {
        outSet.add(pid);
      }
    }
    if (node.children) {
      for (const child of node.children) {
        this._collectPartIds(child, outSet);
      }
    }
  }

  _refreshCheckboxes() {
    const checkboxes = this.container.querySelectorAll('.tree-check');
    for (const cb of checkboxes) {
      const row = cb.parentNode;
      const nodeId = row.dataset.nodeId;
      const nodePartIds = this._nodeIdToPartIds.get(nodeId);
      if (!nodePartIds || nodePartIds.size === 0) {
        cb.checked = false;
        cb.indeterminate = false;
        continue;
      }
      let checked = 0;
      for (const pid of nodePartIds) {
        if (this._checkedPartIds.has(pid)) checked++;
      }
      if (checked === 0) {
        cb.checked = false;
        cb.indeterminate = false;
      } else if (checked === nodePartIds.size) {
        cb.checked = true;
        cb.indeterminate = false;
      } else {
        cb.checked = false;
        cb.indeterminate = true;
      }
    }
  }

  getCheckedPartIds() {
    return this._checkedPartIds;
  }

  getCheckedNodeId() {
    // For backward compatibility: returns the most recently checked node ID,
    // or null if nothing is currently checked or last action was uncheck.
    return this._lastCheckedNodeId;
  }

  clearChecked() {
    this._checkedPartIds = new Set();
    this._lastCheckedNodeId = null;
    this._refreshCheckboxes();
    if (this.callbacks.onCheckChange) {
      this.callbacks.onCheckChange(null, this._checkedPartIds);
    }
  }

  _isNodeFixed(node) {
    if (!node.partIds) return false;
    for (const pid of node.partIds) {
      if (this._fixedPartIds.has(pid)) return true;
    }
    return false;
  }

  _toggleCollapse(nodeId, childrenEl, arrow) {
    if (this._collapsed.has(nodeId)) {
      this._collapsed.delete(nodeId);
      childrenEl.classList.remove('collapsed');
      arrow.textContent = '▼';
    } else {
      this._collapsed.add(nodeId);
      childrenEl.classList.add('collapsed');
      arrow.textContent = '▶';
    }
  }

  _select(el, node) {
    if (this.selectedEl) {
      this.selectedEl.classList.remove('selected');
    }
    this.selectedEl = el;
    this.selectedNodeId = node.id;
    this.selectedPartIds = node.partIds || [];
    el.classList.add('selected');

    if (this.callbacks.onSelect) {
      this.callbacks.onSelect(node.id, this.selectedPartIds);
    }
  }

  getSelectedPartIds() {
    return this.selectedPartIds;
  }

  setFixedPartIds(ids) {
    this._fixedPartIds = new Set(ids);
    this.build(this.hierarchy, Object.values(this.partsMap), []);
  }

  _showColorPicker(swatch, node) {
    const existing = document.querySelector('.color-palette');
    if (existing) existing.remove();

    const lookupId = (node.partIds && node.partIds.length > 0) ? node.partIds[0] : node.id;
    const presets = [
      { label: '红', hex: '#ff0000', rgb: '255,0,0' },
      { label: '绿', hex: '#008040', rgb: '0,128,64' },
      { label: '橙', hex: '#ff8000', rgb: '255,128,0' },
      { label: '蓝', hex: '#0080c0', rgb: '0,128,192' },
      { label: '黄', hex: '#808000', rgb: '128,128,0' },
      { label: '紫', hex: '#800080', rgb: '128,0,128' },
    ];

    const panel = document.createElement('div');
    panel.className = 'color-palette';

    const label = document.createElement('div');
    label.textContent = '预设';
    label.style.fontSize = '11px';
    label.style.color = '#888';
    label.style.marginBottom = '4px';
    panel.appendChild(label);

    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.flexWrap = 'wrap';
    row.style.gap = '4px';

    const _applyColor = (color) => {
      const v = color;
      swatch.style.backgroundColor = v;
      this._colorMap[lookupId] = v;
      if (this.callbacks.onColorChange) {
        const allIds = new Set();
        this._collectPartIds(node, allIds);
        this.callbacks.onColorChange([...allIds], v);
      }
      panel.remove();
      document.removeEventListener('click', _closeOnOutside, true);
    };

    for (const p of presets) {
      const btn = document.createElement('span');
      btn.className = 'color-preset';
      btn.style.backgroundColor = p.hex;
      btn.title = p.label + ' ' + p.hex;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        _applyColor(p.hex);
      });
      row.appendChild(btn);
    }
    panel.appendChild(row);

    const customBtn = document.createElement('div');
    customBtn.className = 'color-custom';
    customBtn.textContent = '自定义...';
    customBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      panel.remove();
      document.removeEventListener('click', _closeOnOutside, true);
      const input = document.createElement('input');
      input.type = 'color';
      input.value = this._colorMap[lookupId] || '#0080c0';
      input.style.position = 'fixed';
      input.style.opacity = '0';
      document.body.appendChild(input);
      input.click();
      input.addEventListener('input', () => _applyColor(input.value));
      input.addEventListener('change', () => { if (input.parentNode) input.remove(); });
    });
    panel.appendChild(customBtn);

    document.body.appendChild(panel);

    const swatchRect = swatch.getBoundingClientRect();
    panel.style.left = Math.min(swatchRect.left, window.innerWidth - 200) + 'px';
    panel.style.top = Math.min(swatchRect.bottom + 4, window.innerHeight - 180) + 'px';

    const _closeOnOutside = (e) => {
      if (!panel.contains(e.target) && e.target !== swatch) {
        panel.remove();
        document.removeEventListener('click', _closeOnOutside, true);
      }
    };
    setTimeout(() => document.addEventListener('click', _closeOnOutside, true), 0);
  }

  getSelected() {
    return this.selectedNodeId;
  }

  _isNodeHidden(node) {
    if (!node.partIds) return false;
    return node.partIds.some(pid => this._hiddenPartIds.has(pid));
  }

  _toggleVisibility(node, eyeEl) {
    const wasHidden = this._isNodeHidden(node);
    if (node.partIds) {
      if (wasHidden) {
        for (const pid of node.partIds) this._hiddenPartIds.delete(pid);
      } else {
        for (const pid of node.partIds) this._hiddenPartIds.add(pid);
      }
    }
    if (eyeEl) {
      eyeEl.textContent = wasHidden ? '●' : '◌';
      eyeEl.title = wasHidden ? '点击隐藏' : '点击显示';
    }
    if (this.callbacks.onVisibilityChange) {
      this.callbacks.onVisibilityChange(node.id, node.partIds || [], !wasHidden);
    }
  }

  getHiddenPartIds() {
    return new Set(this._hiddenPartIds);
  }

  setHiddenPartIds(ids) {
    this._hiddenPartIds = new Set(ids);
    this.build(this.hierarchy, Object.values(this.partsMap), []);
  }

  setAllVisible() {
    this._hiddenPartIds.clear();
    this.build(this.hierarchy, Object.values(this.partsMap), []);
    if (this.callbacks.onVisibilityChange) {
      this.callbacks.onVisibilityChange(null, [], true);
    }
  }

  setAllHidden() {
    for (const node of this.hierarchy) {
      this._collectPartIds(node, this._hiddenPartIds);
    }
    this.build(this.hierarchy, Object.values(this.partsMap), []);
    if (this.callbacks.onVisibilityChange) {
      this.callbacks.onVisibilityChange(null, [], false);
    }
  }

  showOnlySelected() {
    if (!this.selectedNodeId) return;
    this._hiddenPartIds.clear();
    for (const node of this.hierarchy) {
      this._collectAllPartIds(node, this._hiddenPartIds);
    }
    if (this.selectedPartIds) {
      for (const pid of this.selectedPartIds) {
        this._hiddenPartIds.delete(pid);
      }
    }
    this.build(this.hierarchy, Object.values(this.partsMap), []);
    if (this.callbacks.onVisibilityChange) {
      this.callbacks.onVisibilityChange(this.selectedNodeId, this.selectedPartIds || [], true);
    }
  }

  _collectAllPartIds(node, outSet) {
    if (node.partIds) {
      for (const pid of node.partIds) {
        outSet.add(pid);
      }
    }
    if (node.children) {
      for (const child of node.children) {
        this._collectAllPartIds(child, outSet);
      }
    }
  }
}
