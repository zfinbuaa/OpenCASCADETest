/**
 * Tree View — 多级可折叠零件树，支持选择、固定参照、颜色修改。
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
  }

  _renderNode(node, parentEl, depth) {
    const hasChildren = node.children && node.children.length > 0;
    const isLeaf = !hasChildren && node.partIds && node.partIds.length === 1
                   && node.id === node.partIds[0];
    const isFixed = this._isNodeFixed(node);
    const part = this.partsMap[node.id];
    const stage = part ? (part.disassemblyStage || this.stagesMap[node.id]) : this.stagesMap[node.id];
    const isFastener = part ? part.isFastener : false;

    const row = document.createElement('div');
    row.className = 'tree-node';
    row.style.paddingLeft = (12 + depth * 16) + 'px';
    row.dataset.nodeId = node.id;

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

    const count = node.partIds ? node.partIds.length : 0;
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

    if (isLeaf) {
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      const color = this._colorMap[node.id] || (part && part.color
        ? '#' + ((1 << 24) + (Math.round(part.color[0]*255) << 16) + (Math.round(part.color[1]*255) << 8) + Math.round(part.color[2]*255)).toString(16).slice(1)
        : '#bbbbbb');
      swatch.style.backgroundColor = color;
      swatch.title = '点击修改颜色';
      swatch.addEventListener('click', (e) => {
        e.stopPropagation();
        this._showColorPicker(swatch, node.id);
      });
      row.appendChild(swatch);
    }

    row.addEventListener('click', (e) => {
      if (e.target.classList.contains('arrow') && hasChildren) {
        this._toggleCollapse(node.id, childrenEl, arrow);
        return;
      }
      this._select(row, node);
    });

    parentEl.appendChild(row);

    const childrenEl = document.createElement('div');
    childrenEl.className = 'tree-children' + (this._collapsed.has(node.id) ? ' collapsed' : '');
    if (hasChildren) {
      for (const child of node.children) {
        this._renderNode(child, childrenEl, depth + 1);
      }
    }
    parentEl.appendChild(childrenEl);
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

  _showColorPicker(swatch, partId) {
    const input = document.createElement('input');
    input.type = 'color';
    input.value = this._colorMap[partId] || '#bbbbbb';
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

    input.addEventListener('blur', () => {
      setTimeout(() => {
        if (input.parentNode) document.body.removeChild(input);
      }, 200);
    });
  }

  getSelected() {
    return this.selectedNodeId;
  }
}
