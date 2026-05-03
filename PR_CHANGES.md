# PR: Fix Python 2.7 Compatibility for Abaqus 2022

## Summary

This PR fixes multiple Python 2.7 compatibility issues in `abaqus_mcp_plugin.py` that prevented the MCP plugin from functioning correctly in Abaqus 2022 (which ships with Python 2.7). After these changes, the plugin can successfully establish file-based IPC with the MCP server, execute scripts, and create Abaqus models.

## Problem

The original plugin was written primarily for Python 3. When running inside Abaqus 2022's Python 2.7 kernel, the following issues caused complete communication failure:

1. **`io.open()` + `json.dump()` type mismatch** — `io.open(path, 'w', encoding='utf-8')` returns a text writer expecting `unicode`, but `json.dump()` in Python 2.7 outputs `str` (bytes). This caused **silent write failures** — result files were either empty or truncated.

2. **`open('r')` encoding mismatch** — Python 2.7's built-in `open()` reads files using the system default encoding (GBK on Chinese Windows), but command files are written in UTF-8 by the Python 3 MCP server. This caused `UnicodeDecodeError` when reading commands.

3. **`os.replace()` unavailable** — Python 2.7 does not have `os.replace()`, which was used for atomic file replacement in `write_status()`.

4. **`base64.b64encode()` return type** — In Python 2.7, `b64encode()` returns `str`, not `bytes`, so `.decode('ascii')` would fail.

5. **Thread API differences** — `Thread.is_alive()` is `Thread.isAlive()` in Python 2.7.

6. **`json.dump()` stream truncation** — Even after fixing the type mismatch, `json.dump(data, f)` is a streaming operation. In the cooperative loop mode, if the data contained long tracebacks, the write could be interrupted by the next `poll_once()` call, resulting in truncated JSON files.

## Changes

### `abaqus_mcp_plugin.py`

#### 1. File writing: `json.dumps()` + `open('w')` instead of `json.dump()` + `io.open('w')`

**Before:**
```python
def _write_json(path, data):
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
```

**After:**
```python
def _write_json(path, data):
    s = json.dumps(data, indent=2)
    with open(path, 'w') as f:
        f.write(s)
```

**Reason:** In Python 2.7, `io.open('w', encoding='utf-8')` expects `unicode` writes, but `json.dump()` outputs `str`. Using `json.dumps()` to pre-serialize into a complete string, then writing with the built-in `open('w')` (which accepts `str`), avoids the type conflict and also prevents stream truncation.

The same pattern was applied to `write_status()`.

#### 2. File reading: binary mode + multi-encoding fallback

**Before:**
```python
with io.open(cmd_path, 'r', encoding='utf-8-sig') as f:
    return json.load(f)
```

**After:**
```python
with open(cmd_path, 'rb') as f:
    raw = f.read()
for enc in ['utf-8-sig', 'utf-8', 'gbk', 'latin-1']:
    try:
        text = raw.decode(enc)
        return json.loads(text)
    except (UnicodeDecodeError, ValueError):
        continue
```

**Reason:** Python 2.7's `io.open()` with `encoding='utf-8'` raises `UnicodeDecodeError` on non-UTF-8 bytes, and the exception can propagate to outer handlers that don't attempt fallback. Reading as raw bytes and trying multiple encodings ensures maximum compatibility. `latin-1` is the final fallback since it never fails (maps every byte 0x00-0xFF to the corresponding Unicode code point).

This pattern was applied to:
- `_load_command_file()` — reading command JSON files
- `_background_self_test()` — reading result JSON files
- `execute_script()` — reading script files

#### 3. Script file writing: binary mode

**Before:**
```python
with io.open(script_path, 'w', encoding='utf-8') as f:
    f.write(unicode(script_content))
```

**After:**
```python
if isinstance(script_content, bytes):
    raw = script_content
else:
    raw = script_content.encode('utf-8')
with open(script_path, 'wb') as f:
    f.write(raw)
```

**Reason:** `unicode()` is a Python 2 built-in that doesn't exist in Python 3. Using `encode('utf-8')` + binary write works in both versions.

#### 4. `os.replace()` → `os.remove()` + `os.rename()`

**Before:**
```python
os.replace(tmp_file, STATUS_FILE)
```

**After:**
```python
if os.path.exists(STATUS_FILE):
    os.remove(STATUS_FILE)
os.rename(tmp_file, STATUS_FILE)
```

**Reason:** `os.replace()` was added in Python 3.3 and is not available in Python 2.7. The two-step remove+rename achieves the same effect on Windows (with a fallback path if rename fails).

#### 5. `base64.b64encode()` return type handling

**Before:**
```python
data = base64.b64encode(f.read()).decode('ascii')
```

**After:**
```python
data = base64.b64encode(f.read())
if isinstance(data, bytes):
    data = data.decode('ascii')
```

**Reason:** In Python 2.7, `b64encode()` returns `str`, which has no `.decode('ascii')` method in the same way as Python 3 `bytes`. The `isinstance` check handles both versions.

#### 6. Thread API compatibility

**Before:**
```python
thread_obj.daemon = True
thread_obj.is_alive()
```

**After:**
```python
try:
    thread_obj.daemon = True
except Exception:
    thread_obj.setDaemon(True)

try:
    return thread_obj.is_alive()
except Exception:
    return thread_obj.isAlive()
```

**Reason:** Python 2.7 uses `setDaemon()` and `isAlive()` (camelCase), while Python 3 uses `daemon` property and `is_alive()` (snake_case).

#### 7. Added `from __future__ import print_function`

**Reason:** Python 2.7 treats `print` as a statement by default. This import makes it a function, consistent with Python 3 syntax used throughout the codebase.

### `.mcp.json`

Updated to use project-level configuration with explicit Python 3 interpreter path and `ABAQUS_MCP_HOME` environment variable, avoiding C drive space usage from global configuration.

## Testing

All changes were verified end-to-end:

1. **Ping test** — `ping` tool returns `pong (v4.0.0)` ✓
2. **Script execution** — `execute_script` with `print('hello')` returns output ✓
3. **Cantilever beam model** — 3D solid beam (120×20×12mm, C3D8R mesh, Steel material, Encastre BC, tip force) created successfully ✓
4. **Status heartbeat** — `status.json` updates every 2 seconds ✓
5. **No residual temp files** — `status.json.tmp` is properly cleaned up ✓

## Files Changed

| File | Change Type |
|------|------------|
| `abaqus_mcp_plugin.py` | Python 2.7 compatibility fixes (6 categories) |
| `.mcp.json` | Project-level MCP configuration |

## Backward Compatibility

All changes are backward-compatible with Python 3. The patterns used (`json.dumps()` + `f.write()`, binary read + manual decode, `isinstance` checks) work identically in both Python 2.7 and Python 3.

---

## Lessons Learned: Why AI-Generated Abaqus Scripts Fail

During testing, the cantilever beam script was rewritten multiple times before succeeding. The author's reference script (`cantilever_beam_abaqus.py`) worked on the first try. This section documents the specific mistakes and the principles they reveal, so future MCP-based Abaqus scripting can avoid the same pitfalls.

### Mistake 1: Choosing Beam/Wire Elements Over Solid Elements

**Failed approach:**
```python
s.Line(point1=(0.0, 0.0), point2=(1.0, 0.0))
p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseWire(sketch=s)
m.BeamProfile(name='RectProfile', shape=RECTANGULAR, a=0.05, b=0.01)
m.BeamSection(name='BeamSection', profile='RectProfile', material='Steel', section=SectionBeam())
```

**Author's approach:**
```python
sketch.rectangle(point1=(0.0, 0.0), point2=(WIDTH_MM, HEIGHT_MM))
part = model.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
part.BaseSolidExtrude(sketch=sketch, depth=LENGTH_MM)
model.HomogeneousSolidSection(name='SteelSection', material='Steel', thickness=None)
```

**Why it failed:**

1. **`m.BeamProfile()` does not exist in Abaqus 2022.** The correct API is `m.RectangularProfile()`, `m.CircularProfile()`, etc. — each shape has its own constructor. `BeamProfile` is a generic term from documentation, not a callable function. This caused `AttributeError: 'Model' object has no attribute 'BeamProfile'`.

2. **Beam element setup requires 3 extra steps** that solid elements don't: creating a profile, creating a beam section with `SectionBeam()`, and calling `assignBeamSectionOrientation()`. Each step is a potential failure point, and the API varies between Abaqus versions.

3. **Solid elements are more universal.** `HomogeneousSolidSection` + `BaseSolidExtrude` has been stable across Abaqus versions and requires fewer API calls. For a simple cantilever beam, there is no reason to use wire/beam elements unless you specifically need beam theory results.

**Principle: Prefer solid elements over beam/wire elements for MCP-generated scripts.** Solid modeling uses a simpler, more stable API surface. Only use beam elements when the simulation specifically demands Euler-Bernoulli or Timoshenko beam theory.

### Mistake 2: Fragile Geometry Selection

**Failed approach:**
```python
left_vertex = a.instances['Beam-1'].vertices[0]
right_vertex = a.instances['Beam-1'].vertices[1]
```

**Author's approach:**
```python
fixed_face = part.faces.findAt(((WIDTH_MM / 2.0, HEIGHT_MM / 2.0, 0.0),))
free_end_nodes = instance.nodes.getByBoundingBox(
    xMin=-1.0e-6, xMax=WIDTH_MM + 1.0e-6,
    yMin=-1.0e-6, yMax=HEIGHT_MM + 1.0e-6,
    zMin=LENGTH_MM - 1.0e-6, zMax=LENGTH_MM + 1.0e-6,
)
```

**Why it failed:**

1. **Index-based selection (`vertices[0]`, `vertices[1]`) is unreliable.** The ordering of vertices in Abaqus is not guaranteed to match geometric intuition. Vertex `[0]` might not be at `x=0`.

2. **`findAt()` with coordinates is deterministic.** Given a coordinate tuple, Abaqus returns the face/edge/vertex closest to that point. This is robust regardless of internal ordering.

3. **`getByBoundingBox()` for node selection is essential for distributed loads.** Applying a concentrated force to a single vertex on a solid element face creates a stress singularity. The author distributes the force across all free-end nodes: `cf2=TIP_FORCE_N / float(n_nodes)`.

**Principle: Always use `findAt()` with coordinates or `getByBoundingBox()` for geometric selection. Never rely on index-based access (`vertices[0]`, `edges[:]`).**

### Mistake 3: Missing Explicit Mesh Control

**Failed approach:**
```python
# No mesh control at all — relied on Abaqus defaults
```

**Author's approach:**
```python
part.seedPart(size=SEED_SIZE_MM, deviationFactor=0.1, minSizeFactor=0.1)
part.setMeshControls(regions=part.cells, elemShape=HEX)
elem_type = mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD)
part.setElementType(regions=(part.cells,), elemTypes=(elem_type,))
part.generateMesh()
```

**Why it matters:**

1. **Without `setMeshControls(elemShape=HEX)`, Abaqus may default to tetrahedral elements (C3D10M)**, which have different convergence characteristics and are not what most structural analyses expect.

2. **Without `setElementType(C3D8R)`, the element type is determined by Abaqus defaults**, which may vary depending on the analysis type and part geometry.

3. **Without `generateMesh()`, the mesh is not created until the job is submitted.** While Abaqus will auto-mesh at submission time, explicit meshing allows verification before running an expensive analysis.

**Principle: Always specify mesh seed size, element shape, and element type explicitly. Never rely on defaults.**

### Mistake 4: Missing `DatumCsysByDefault`

**Failed approach:**
```python
# No coordinate system definition
```

**Author's approach:**
```python
assembly.DatumCsysByDefault(CARTESIAN)
```

**Why it matters:** While Abaqus creates a default Cartesian system, explicitly defining it ensures consistent behavior across different Abaqus versions and session states. Some operations (like `findAt()` coordinate lookups) depend on the assembly coordinate system being properly initialized.

**Principle: Always define the assembly coordinate system explicitly before creating sets or applying BCs.**

### Mistake 5: Applying Force to a Single Point on a Solid Model

**Failed approach:**
```python
m.ConcentratedForce(name='TipLoad', createStepName='LoadStep',
                    region=tip_region, cf2=-1000.0)
```

**Author's approach:**
```python
free_end_nodes = instance.nodes.getByBoundingBox(...)
assembly.Set(name='free_end_nodes', nodes=free_end_nodes)
n_nodes = len(free_end_nodes)
model.ConcentratedForce(name='TipForce', createStepName='Load',
                        region=assembly.sets['free_end_nodes'],
                        cf2=TIP_FORCE_N / float(n_nodes))
```

**Why it matters:**

1. **A single-node concentrated force on a 3D solid element creates a stress singularity** — the stress at that point is theoretically infinite, making results mesh-dependent and physically meaningless.

2. **Distributing the force across all nodes on the free-end face** approximates a uniform pressure load, which is physically realistic and produces convergent results.

3. **The author uses `getByBoundingBox()` with small tolerances (`±1e-6`)** to select all nodes on the free-end face, then divides the total force by the number of nodes.

**Principle: For solid models, always distribute concentrated forces across multiple nodes. Use `getByBoundingBox()` to select face nodes, then divide the total force by the node count.**

### Summary: Abaqus Scripting Checklist for MCP

| Step | Correct Approach | Common Mistake |
|------|-----------------|----------------|
| Part creation | `BaseSolidExtrude` with rectangular sketch | `BaseWire` with line sketch |
| Section | `HomogeneousSolidSection` | `BeamSection` with `SectionBeam()` |
| Geometry selection | `findAt((coordinates,))` | `vertices[0]` index access |
| Node selection | `getByBoundingBox()` with tolerances | Single vertex |
| Mesh control | Explicit `seedPart` + `setMeshControls(HEX)` + `setElementType(C3D8R)` | Rely on defaults |
| Coordinate system | `DatumCsysByDefault(CARTESIAN)` | Omit |
| Force application | Distribute across face nodes | Single-point concentrated force |
| Model cleanup | `if name in mdb.models: del mdb.models[name]` | Assume clean state |
