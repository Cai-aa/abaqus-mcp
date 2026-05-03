import json
import os
import time
import uuid

C = r'E:\Song\2fin-L\abaqus-mcp\commands'
R = r'E:\Song\2fin-L\abaqus-mcp\results'
cid = uuid.uuid4().hex[:8]

script = """from abaqus import *
from abaqusConstants import *
import mesh
import regionToolset

LENGTH_MM = 120.0
WIDTH_MM = 20.0
HEIGHT_MM = 12.0
YOUNGS_MODULUS_MPA = 210000.0
POISSONS_RATIO = 0.3
TIP_FORCE_N = -800.0
SEED_SIZE_MM = 6.0

if 'CantileverBeam' in mdb.models.keys():
    del mdb.models['CantileverBeam']

model = mdb.Model(name='CantileverBeam')

sketch = model.ConstrainedSketch(name='beam_profile', sheetSize=240.0)
sketch.rectangle(point1=(0.0, 0.0), point2=(WIDTH_MM, HEIGHT_MM))

part = model.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
part.BaseSolidExtrude(sketch=sketch, depth=LENGTH_MM)

del model.sketches['beam_profile']

material = model.Material(name='Steel')
material.Elastic(table=((YOUNGS_MODULUS_MPA, POISSONS_RATIO),))

model.HomogeneousSolidSection(name='SteelSection', material='Steel', thickness=None)

part.SectionAssignment(
    region=regionToolset.Region(cells=part.cells),
    sectionName='SteelSection',
    offset=0.0,
    offsetType=MIDDLE_SURFACE,
    offsetField='',
    thicknessAssignment=FROM_SECTION,
)

fixed_face = part.faces.findAt(((WIDTH_MM / 2.0, HEIGHT_MM / 2.0, 0.0),))
part.Set(faces=fixed_face, name='fixed_end')

free_face = part.faces.findAt(((WIDTH_MM / 2.0, HEIGHT_MM / 2.0, LENGTH_MM),))
part.Set(faces=free_face, name='free_end')

part.seedPart(size=SEED_SIZE_MM, deviationFactor=0.1, minSizeFactor=0.1)
part.setMeshControls(regions=part.cells, elemShape=HEX)
elem_type = mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD)
part.setElementType(regions=(part.cells,), elemTypes=(elem_type,))
part.generateMesh()

assembly = model.rootAssembly
assembly.DatumCsysByDefault(CARTESIAN)
instance = assembly.Instance(name='Beam-1', part=part, dependent=ON)

assembly.Set(name='fixed_end', faces=instance.faces.findAt(((WIDTH_MM / 2.0, HEIGHT_MM / 2.0, 0.0),)))

free_end_face = instance.faces.findAt(((WIDTH_MM / 2.0, HEIGHT_MM / 2.0, LENGTH_MM),))
assembly.Set(name='free_end', faces=free_end_face)

free_end_nodes = instance.nodes.getByBoundingBox(
    xMin=-1.0e-6, xMax=WIDTH_MM + 1.0e-6,
    yMin=-1.0e-6, yMax=HEIGHT_MM + 1.0e-6,
    zMin=LENGTH_MM - 1.0e-6, zMax=LENGTH_MM + 1.0e-6,
)
assembly.Set(name='free_end_nodes', nodes=free_end_nodes)

model.StaticStep(name='Load', previous='Initial')

model.EncastreBC(name='Fixed', createStepName='Initial', region=assembly.sets['fixed_end'])

n_nodes = len(free_end_nodes)
model.ConcentratedForce(
    name='TipForce',
    createStepName='Load',
    region=assembly.sets['free_end_nodes'],
    cf2=TIP_FORCE_N / float(n_nodes),
)

mdb.Job(name='CantileverJob', model='CantileverBeam', type=ANALYSIS,
        description='Cantilever beam static analysis')

print('Cantilever beam model created successfully!')
print('3D solid: %d x %d x %d mm' % (LENGTH_MM, WIDTH_MM, HEIGHT_MM))
print('Material: Steel E=%s MPa, nu=%s' % (YOUNGS_MODULUS_MPA, POISSONS_RATIO))
print('Mesh: C3D8R, seed=%s mm, %d nodes at free end' % (SEED_SIZE_MM, n_nodes))
print('BC: Encastre at z=0')
print('Load: %.1f N at free end (Y)' % TIP_FORCE_N)
"""

cmd = {'id': cid, 'type': 'execute_script', 'script': script, 'timestamp': time.time()}
cp = os.path.join(C, 'cmd_%s.json' % cid)
rp = os.path.join(R, '%s.json' % cid)

with open(cp, 'w', encoding='utf-8') as f:
    json.dump(cmd, f, ensure_ascii=False)
print('Sent id=%s' % cid)

dl = time.time() + 60
ok = False
while time.time() < dl:
    if os.path.exists(rp):
        r = json.load(open(rp, encoding='utf-8'))
        os.remove(rp)
        print('Result: ' + json.dumps(r, indent=2, ensure_ascii=False))
        ok = True
        break
    time.sleep(0.1)
if not ok:
    print('TIMEOUT')
    if os.path.exists(cp):
        os.remove(cp)
