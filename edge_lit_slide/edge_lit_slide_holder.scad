// Slide-sized edge-light holder for a 75 x 25 x 3 mm acrylic light guide.
// The LED enters through the left short edge. Print in opaque black material.
// Change only the parameters below for a measured plate or LED.

$fn = 64;

guide_length = 75.0;
guide_width = 25.0;
guide_thickness = 3.0;
guide_clearance = 0.30;

led_diameter = 5.0;       // conventional round LED body
led_clearance = 0.25;
led_lead_slot = 2.2;

floor_thickness = 1.0;
wall = 1.5;
end_wall = 1.2;
led_bay_length = 8.0;
top_lip = 0.55;
lip_depth = 0.65;

cavity_x = led_bay_length + end_wall;
cavity_y = wall;
cavity_z = floor_thickness;
cavity_l = guide_length + guide_clearance;
cavity_w = guide_width + guide_clearance;
cavity_h = guide_thickness + guide_clearance;

outer_l = cavity_x + cavity_l + end_wall;
outer_w = cavity_w + 2 * wall;
outer_h = cavity_z + cavity_h + top_lip;
led_axis_z = cavity_z + guide_thickness / 2;

module holder() {
  difference() {
    union() {
      cube([outer_l, outer_w, outer_h]);
    }

    // Guide cavity is open at the top; it can be pressed under the two lips.
    translate([cavity_x, cavity_y, cavity_z])
      cube([cavity_l, cavity_w, cavity_h + top_lip + 0.1]);

    // Open most of the floor so only a narrow support frame remains. This
    // allows a removable white reflector card to sit below the light guide.
    translate([cavity_x + 2.0, cavity_y + 2.0, -0.1])
      cube([cavity_l - 4.0, cavity_w - 4.0, floor_thickness + 0.2]);

    // Round LED bore; its axis is centered on the acrylic thickness.
    translate([-0.1, outer_w / 2, led_axis_z])
      rotate([0, 90, 0])
        cylinder(h = cavity_x + 0.2, d = led_diameter + led_clearance);

    // Lead/wire escape slot down through the bottom of the LED bay.
    translate([1.0, outer_w / 2 - led_lead_slot / 2, -0.1])
      cube([led_bay_length - 1.0, led_lead_slot, led_axis_z + 0.1]);
  }

  // Retaining lips along long sides; leave the optical field unobstructed.
  translate([cavity_x, cavity_y, cavity_z + cavity_h])
    cube([cavity_l, lip_depth, top_lip]);
  translate([cavity_x, cavity_y + cavity_w - lip_depth, cavity_z + cavity_h])
    cube([cavity_l, lip_depth, top_lip]);
}

holder();
