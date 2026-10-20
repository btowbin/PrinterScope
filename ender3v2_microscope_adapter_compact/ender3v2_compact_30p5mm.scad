// R3: unobstructed mounting access, original 12 mm screw-bearing thickness.
// X left/right, Y forward from carriage, Z upward; screw line Z=0.
$fn=192;
part="base"; // "base", "cap", or "assembly"
barrel_diameter=30.5;
bore_diameter=30.6;
ri=bore_diameter/2;
ro=20.5;
seat_depth=12;
plate_depth=16;
cy=plate_depth+ri;
split_relief=1; // 2 mm total tightening gap
zb=-8;
zt=14;

module block(size,centre) {translate(centre) cube(size,center=true);}
module along_y(d,y0,y1,x=0,z=0) {
    translate([x,(y0+y1)/2,z]) rotate([90,0,0])
        cylinder(d=d,h=y1-y0,center=true);
}
module slot(d,length,y0,y1,x,z=0) {
    hull() for(dx=[-(length-d)/2,(length-d)/2])
        along_y(d,y0,y1,x+dx,z);
}
module nut_pocket(x) {
    // M4 hex nut: 7.4 mm across flats, 3.5 mm depth, rear insertion.
    translate([x,cy-8+3.5/2,3]) rotate([90,0,0])
        cylinder(d=7.4/cos(30),h=3.5,center=true,$fn=6);
}
module base() {
    difference() {
        union() {
            // Keep the carriage-contact plate below the upper roller heads.
            block([56,plate_depth,16],[0,plate_depth/2,0]);
            intersection() {
                translate([0,cy,zb]) cylinder(r=ro,h=zt-zb);
                block([70,cy-split_relief+1,zt-zb],
                      [0,(cy-split_relief-1)/2,(zb+zt)/2]);
            }
            for(x=[-23.5,23.5]) block([9,7,19],[x,cy-4.5,1.5]);
        }
        translate([0,cy,zb-1]) cylinder(r=ri,h=zt-zb+2);
        for(x=[-7,7]) {
            slot(3.6,6.8,-1,seat_depth+0.01,x);
            // Clear head and Allen key through all rear-clamp material.
            slot(6.8,10,seat_depth,65,x);
        }
        for(x=[-23.5,23.5]) {
            along_y(4.4,cy-9,cy+1,x,3);
            nut_pocket(x);
        }
    }
}
module cap() {
    difference() {
        union() {
            intersection() {
                translate([0,cy,zb]) cylinder(r=ro,h=zt-zb);
                block([70,ro+2,zt-zb],
                      [0,cy+split_relief+(ro+2)/2,(zb+zt)/2]);
            }
            for(x=[-23.5,23.5]) block([9,7,19],[x,cy+4.5,1.5]);
        }
        translate([0,cy,zb-1]) cylinder(r=ri,h=zt-zb+2);
        for(x=[-23.5,23.5]) along_y(4.4,cy,cy+9,x,3);
    }
}
if(part=="base") base();
if(part=="cap") cap();
if(part=="assembly") {base(); color("orange") cap();}
