// Ender 3 V2 universal microscope adapter — quick-test version
// Coordinate system when installed:
//   X = carriage left/right, Y = forward from carriage, Z = vertical.
// Print with the flat rear face on the build plate.

$fn = 72;

plate_width = 60;
plate_height = 90;
plate_thickness = 5;

// Stock MK8 hot-end mounting screws. The slots tolerate small carriage revisions.
mount_spacing = 14;
mount_slot_length = 7;
mount_hole_diameter = 3.8;

// Two V-cradles support a wide range of cylindrical optical tubes.
cradle_width = 54;
cradle_depth = 22;
cradle_band_height = 14;
cradle_centres = [-24, 24];
v_vertex_y = 7;
v_slope = 0.55;

// Slots accept heavy 7.6 mm cable ties. Use 300 mm ties or longer.
tie_slot_width = 5;
tie_slot_height = 11;
tie_slot_x = 23;

module rounded_slot_x(length, diameter, depth) {
    hull() {
        translate([-length/2 + diameter/2, 0, 0])
            rotate([90,0,0]) cylinder(h=depth, d=diameter, center=true);
        translate([ length/2 - diameter/2, 0, 0])
            rotate([90,0,0]) cylinder(h=depth, d=diameter, center=true);
    }
}

module cradle_band(zc) {
    // The V-shaped cutout opens toward +Y and needs no print support.
    difference() {
        translate([-cradle_width/2, plate_thickness, zc-cradle_band_height/2])
            cube([cradle_width, cradle_depth-plate_thickness, cradle_band_height]);

        // Large triangular prism forming the V seat.
        translate([0, v_vertex_y, zc-cradle_band_height])
            linear_extrude(height=cradle_band_height*3)
                polygon(points=[
                    [0,0],
                    [80,80*v_slope],
                    [-80,80*v_slope]
                ]);
    }
}

module adapter() {
    difference() {
        union() {
            translate([-plate_width/2, 0, -plate_height/2])
                cube([plate_width, plate_thickness, plate_height]);
            for (zc = cradle_centres) cradle_band(zc);
        }

        // Two M3 horizontal adjustment slots, nominally 14 mm apart.
        for (xc = [-mount_spacing/2, mount_spacing/2])
            translate([xc, plate_thickness/2, 0])
                rounded_slot_x(mount_slot_length, mount_hole_diameter,
                               plate_thickness+4);

        // Paired tie slots at each cradle level.
        for (zc = cradle_centres)
            for (xc = [-tie_slot_x, tie_slot_x])
                translate([xc-tie_slot_width/2, -1, zc-tie_slot_height/2])
                    cube([tie_slot_width, cradle_depth+2, tie_slot_height]);
    }
}

adapter();

