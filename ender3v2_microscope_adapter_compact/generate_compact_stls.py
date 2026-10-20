"""Smooth R3 geometry, STL generation, collision checks, and preview."""
from pathlib import Path
import sys, json, math
# Use the existing numpy so the existing plotting installation stays compatible.
import numpy as np
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent / '.cad-deps'))
import trimesh as tm
from shapely.geometry import LineString

CY=31.3
RI=15.3
RO=20.5
BOTTOM=-8.0
TOP=14.0
SEAT=12.0
RES=192

def box(size,centre):
    m=tm.creation.box(extents=size); m.apply_translation(centre); return m

def y_cyl(d,y0,y1,x=0,z=0,sections=RES):
    m=tm.creation.cylinder(radius=d/2,height=y1-y0,sections=sections)
    m.apply_transform([[1,0,0,x],[0,0,1,(y0+y1)/2],[0,-1,0,z],[0,0,0,1]])
    return m

def slot(d,length,y0,y1,x,z=0):
    half=(length-d)/2
    polygon=LineString([(-half,0),(half,0)]).buffer(d/2,quad_segs=48)
    m=tm.creation.extrude_polygon(polygon,y1-y0,engine='earcut')
    m.apply_transform([[1,0,0,x],[0,0,1,y0],[0,-1,0,z],[0,0,0,1]])
    return m

def union(parts): return tm.boolean.union(parts,engine='manifold')
def diff(a,cutters): return tm.boolean.difference([a,*cutters],engine='manifold')
def intersect(a,b): return tm.boolean.intersection([a,b],engine='manifold')

def ring_blank():
    m=tm.creation.cylinder(radius=RO,height=TOP-BOTTOM,sections=RES)
    m.apply_translation([0,CY,(TOP+BOTTOM)/2]); return m

def bore():
    m=tm.creation.cylinder(radius=RI,height=TOP-BOTTOM+2,sections=RES)
    m.apply_translation([0,CY,(TOP+BOTTOM)/2]); return m

def parts():
    rb=ring_blank()
    base_half=intersect(rb,box([70,CY,22],[0,(CY-2)/2,3]))
    cap_half=intersect(rb,box([70,RO+2,22],[0,CY+1+(RO+2)/2,3]))
    base=union([box([56,16,16],[0,8,0]),base_half,
                *[box([9,7,19],[x,CY-4.5,1.5]) for x in [-23.5,23.5]]])
    cap=union([cap_half,*[box([9,7,19],[x,CY+4.5,1.5]) for x in [-23.5,23.5]]])
    cuts=[bore()]
    for x in [-7,7]:
        cuts += [slot(3.6,6.8,-1,SEAT+0.01,x),slot(6.8,10,SEAT,65,x)]
    for x in [-23.5,23.5]:
        cuts += [y_cyl(4.4,CY-9,CY+1,x,3),
                 y_cyl(7.4/math.cos(math.pi/6),CY-8,CY-4.5,x,3,6)]
    base=diff(base,cuts)
    cap=diff(cap,[bore(),*[y_cyl(4.4,CY,CY+9,x,3) for x in [-23.5,23.5]]])
    return base,cap

def collision_volume(a,b):
    hit=intersect(a,b)
    return abs(hit.volume) if len(hit.faces) else 0.0

def screw(x,length=16,head_d=6,head_h=3):
    return union([y_cyl(3,SEAT-length,SEAT-0.02,x),
                  y_cyl(head_d,SEAT+0.02,SEAT+head_h,x)])

def validate(base,cap):
    report={'revision':'R3 smooth mesh','checks':{}}
    checks=report['checks']
    for name,m in [('base',base),('cap',cap)]:
        checks[name]={'watertight':bool(m.is_watertight),
                      'positive_volume':bool(m.volume>0),
                      'connected_bodies':len(m.split()),
                      'bounds_mm':m.bounds.round(4).tolist()}
        assert m.is_watertight and m.volume>0 and len(m.split())==1
        assert m.bounds[0,2]>=-8.001
    screw_hits=[]; key_hits=[]; insertion_hits=[]
    for xnom in [-7,7]:
        for dx in [-1.3,0,1.3]:
            x=xnom+dx
            sv=collision_volume(base,screw(x))
            kv=collision_volume(base,y_cyl(3.0,SEAT+1,65,x))
            iv=collision_volume(base,y_cyl(6.0,SEAT+0.02,65,x))
            screw_hits.append(sv); key_hits.append(kv)
            insertion_hits.append(iv)
            assert sv<0.001 and kv<0.001 and iv<0.001,(x,sv,kv,iv)
    checks['M3_screw_clearance_max_intersection_mm3']=max(screw_hits)
    checks['Allen_key_access_max_intersection_mm3']=max(key_hits)
    checks['screw_head_insertion_max_intersection_mm3']=max(insertion_hits)
    tube=tm.creation.cylinder(radius=15.25,height=22,sections=192)
    tube.apply_translation([0,CY-0.05,3])
    initial=collision_volume(base,tube)+collision_volume(cap,tube)
    assert initial<0.005,initial
    tightened=cap.copy(); tightened.apply_translation([0,-0.4,0])
    interference=collision_volume(tightened,tube)
    assert interference>1 and collision_volume(base,tightened)<0.001
    checks['tube_diameter_mm']=30.5
    checks['uncompressed_bore_mm']=30.6
    checks['tube_initial_collision_mm3']=initial
    checks['cap_closure_at_grip_test_mm']=0.4
    checks['grip_test_interference_mm3']=interference
    checks['split_gap_remaining_at_grip_test_mm']=1.6
    for x in [-23.5,23.5]:
        shank=y_cyl(4,CY-8,CY+8,x,3)
        head=y_cyl(7,CY+8.02,CY+12,x,3)
        for m in [base,cap]:
            assert collision_volume(m,shank)<0.005
            assert collision_volume(m,head)<0.005
        nut=y_cyl(7/math.cos(math.pi/6),CY-7.8,CY-4.6,x,3,6)
        assert collision_volume(base,nut)<0.005
    checks['M4x16_and_hex_nut_clearance']='pass'
    checks['M3_thread_projection_mm']={'L16':4,'L18':6,'L12':0}
    checks['screw_bearing_thickness_mm']=12
    checks['below_screw_line_mm']=8
    report['limits']=[
        'Digital geometry checks only; no physical printer or hardware available.',
        '14 mm carriage hole spacing remains nominal, with +/-1.3 mm validated adjustment per hole.',
        'Original screw length was not measured; M3x12 cannot engage through a 12 mm bearing thickness.',
        'Print tolerances and tube friction require a physical fit test.',
        'Objective projection is unknown: adapter clearance alone cannot certify homing with a specimen.'
    ]
    (HERE/'validation_report.json').write_text(json.dumps(report,indent=2))
    return report

def preview(base,cap):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    fig=plt.figure(figsize=(12,5))
    ax=fig.add_subplot(121,projection='3d')
    for m,col in [(base,'#577b9b'),(cap,'#f2a343')]:
        ax.add_collection3d(Poly3DCollection(m.triangles,facecolor=col,
                                            edgecolor='none',alpha=1))
    ax.set(xlim=(-30,30),ylim=(0,53),zlim=(-8,16))
    ax.set_box_aspect((60,53,24)); ax.view_init(elev=38,azim=65)
    ax.set_xlabel('X (mm)'); ax.set_ylabel('Y (mm)'); ax.set_zlabel('Z (mm)')
    ax.set_title('R3 assembled clamp — 30.5 mm barrel')
    ax2=fig.add_subplot(122)
    for m,col in [(base,'#577b9b'),(cap,'#f2a343')]:
        section=m.section(plane_origin=[0,0,0],plane_normal=[0,0,1])
        for entity in section.entities:
            points=section.vertices[entity.points]
            ax2.plot(points[:,0],points[:,1],color=col,lw=2)
    for x in [-7,7]:
        ax2.add_patch(plt.Rectangle((x-1.5,-4),3,16,color='#999999'))
        ax2.add_patch(plt.Rectangle((x-3,12),6,3,color='#555555'))
        ax2.plot([x,x],[15,38],'--',color='#333333',lw=1)
    ax2.add_patch(plt.Circle((0,CY-0.05),15.25,fill=False,color='#3a8d48',lw=2))
    ax2.set(xlim=(-30,30),ylim=(-5,54),xlabel='X (mm)',ylabel='Forward from carriage (mm)')
    ax2.set_aspect('equal');ax2.grid(alpha=.2)
    ax2.set_title('Z=0 section: screws and tube clearance')
    fig.tight_layout();fig.savefig(HERE/'compact_adapter_preview.png',dpi=180)
    plt.close(fig)

if __name__=='__main__':
    base,cap=parts()
    validate(base,cap)
    for name,m in [('base',base),('cap',cap)]:
        m.export(HERE/f'ender3v2_compact_{name}_30p5mm.stl')
        printable=m.copy();printable.apply_translation([0,0,8])
        printable.export(HERE/f'PRINT_{name}_R3.stl')
        print(name,len(m.faces),'triangles; watertight',m.is_watertight)
    preview(base,cap)
    print('All clearance, grip and manifold checks passed.')
