from ork_parser import Rocket
import difflib

#Colors for printing
GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"


## Check materials for all relevant components ##
def check_materials(rocket):
    expected = {
        "Cardboard": ["bodyTube", "motorTube"],
        "Birch": ["fins", "centeringRing"],
        "PLA": ["nosecone"],
        "Ripstop": ["parachute"],
        "Nylon": ["shockCord"]
    }

    comp_num = 0
    good_comps = 0
    flags = {}
    for material, parts in expected.items():
        for part in parts:
            for comp in rocket.components(part):
                comp_num +=1
                comp_material = comp.get_param("Material")
                if material.lower() in comp_material.lower():
                    good_comps += 1
                    # print(f"{GREEN}OK{RESET}  {comp.name}: {comp_material}")
                else:
                    # print(f"{RED}WRONG{RESET} {comp.name}: {comp_material} (expected {material})")
                    flags[comp.name] = [comp_material, material] # [Actual material, expected]

    #raise flags
    if flags != {}:
        print(f"----- {RED}MATERIAL FLAGS{RESET} -----")
        for comp, vals in flags.items():
            print(f"{comp}:  {RED}{vals[0]}{RESET} should be {vals[1]}")
    else:
        print(f"{'Materials:':<12}{GREEN}OK{RESET}")


def check_nosecone(rocket):
    nc = rocket.noseCone
    flags = {}  # Will contain Parameter: [value, message]
    
    if nc.get_param("Length", "in") > 8.5:                                                                                                      #Length
        flags["Length"] = [round(nc.get_param("Length", "in"), 3), "is greater than the length limit 8.5in"]                                    
    if nc.get_param("aftDiameter", "in") != 3.16:                                                                                               #Diam
        flags["Base Diameter"] = [round(nc.get_param("aftDiameter", "in"), 3), "is not the correct diameter, should be 3.16in"]                 
    if nc.get_param("thickness", "in") != 0.1:                                                                                                  #Thickness
        flags["Thickness"] = [round(nc.get_param("thickness", "in"), 3), "is not the correct thickness, should be 0.1in"]
    if abs(nc.get_param("aft shoulder length", "in") - 3) >= 0.01:   #small tolerance test because ork sometimes stores as long decimal         #Shoulder Length
        flags["Shoulder Length"] = [round(nc.get_param("aft shoulder length", "in"), 5), "shoulder should be 1 cal (3 in) in length"]           
    if nc.get_param("Aft shoulder thickness", "in") != 0.1:                                                                                     #Shoulder Thickness
        flags["Shoulder Thickness"] = [round(nc.get_param("aft shoulder thickness", "in"), 3), "is not the correct thickness, should be 0.1in"]
    if round(nc.get_param("aft shoulder radius", "in"), 3)*2 != 3:                                                                              #Shoulder Diam
        flags["Shoulder Diameter"] = [round(nc.get_param("aft shoulder radius", "in"), 3)*2, "is not the correct diameter, should be 3in"]

    #raise flags
    if flags != {}:
        print(f"----- {RED}NOSECONE FLAGS{RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Nosecone:':<12}{GREEN}OK{RESET}")


def check_body_tube(rocket):
    bt = rocket.bodyTube
    flags = {}
    
    if bt.get_param("Length", "in") > 36:                                                                                                       #Length
        flags["Length"] = [round(bt.get_param("Length", "in"), 3), "is greater than the length limit 36in"]                                    
    if bt.get_param("Diameter", "in") != 3.16:                                                                                                  #Diam
        flags["Base Diameter"] = [round(bt.get_param("aftDiameter", "in"), 3), "is not the correct diameter, should be 3.16in"]                 
    if abs(bt.get_param("thickness", "in") - 0.08) >= 0.001:                                                                                                 #Thickness
        flags["Thickness"] = [round(bt.get_param("thickness", "in"), 3), "is not the correct thickness, should be 0.08in"]

    if flags != {}:
        print(f"----- {RED}BODY TUBE FLAGS{RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Body Tube:':<12}{GREEN}OK{RESET}")

def check_parachute(rocket):
    chute = rocket.parachute
    flags = {}
    tol = 0.01  # inches

    if abs(chute.get_param("Diameter", "in") - 24) >= tol:                                                                                      #Diameter
        flags["Diameter"] = [round(chute.get_param("Diameter", "in"), 3), "is not the correct diameter, should be 24in"]
    if chute.get_param("Line count") != 6:                                                                                                      #Number of Lines
        flags["Number of Lines"] = [chute.get_param("Line count"), "is not the correct number of lines, should be 6"]
    if abs(chute.get_param("Line length", "in") - 30) >= tol:                                                                                   #Line Length
        flags["Line Length"] = [round(chute.get_param("Line length", "in"), 3), "is not the correct line length, should be 30in"]
    line_mat = chute.materials.get("linematerial")
    line_name = line_mat.name if line_mat else "none"
    if "elastic cord" not in line_name.lower():                                                                                                 #Shroud Line Material
        flags["Shroud Line Material"] = [line_name, "is not the correct material, should be Elastic cord"]
    if abs(chute.get_param("Packed length", "in") - 5) >= tol:                                                                                  #Packed Length
        flags["Packed Length"] = [round(chute.get_param("Packed length", "in"), 3), "is not the correct packed length, should be 5in"]
    if abs(chute.get_param("Packed radius", "in") * 2 - 2.5) >= tol:                                                                            #Packed Diameter
        flags["Packed Diameter"] = [round(chute.get_param("Packed radius", "in") * 2, 3), "is not the correct packed diameter, should be 2.5in"]
    if chute.get_param("Deploy event") != "apogee":                                                                                             #Deployment
        flags["Deploys At"] = [chute.get_param("Deploy event"), "is not the correct deployment event, should be apogee"]

    if flags != {}:
        print(f"----- {RED}PARACHUTE FLAGS{RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Parachute:':<12}{GREEN}OK{RESET}")

def check_shock_cord(rocket):
    sc = rocket.shockCord
    flags = {}
    tol = 0.01  # inches

    if sc.is_auto("Cord length"):                                                                                                               #Cord Length
        flags["Shock Cord Length"] = ["auto", "is set to automatic, should be set manually to 144in"]
    else:
        cord = sc.get_param("Cord length", "in")
        if cord is None:
            flags["Shock Cord Length"] = ["missing", "no cord length set in the design"]
        elif abs(cord - 144) >= tol:
            flags["Shock Cord Length"] = [round(cord, 3), "is not the correct length, should be 144in"]
    mat = sc.get_param("Material", default="none").lower()
    if not ("braid" in mat and "nylon" in mat):                                                                                                 #Material
        flags["Shock Cord Material"] = [sc.get_param("Material", default="none"), "is not the correct material, should be Braided nylon"]
    if abs(sc.get_param("Packed length", "in") - 4) >= tol:                                                                                     #Packed Length
        flags["Packed Length"] = [round(sc.get_param("Packed length", "in"), 3), "is not the correct packed length, should be 4in"]
    if abs(sc.get_param("Packed radius", "in") * 2 - 2) >= tol:                                                                                 #Packed Diameter
        flags["Packed Diameter"] = [round(sc.get_param("Packed radius", "in") * 2, 3), "is not the correct packed diameter, should be 2in"]

    if flags != {}:
        print(f"----- {RED}SHOCK CORD FLAGS{RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Shock Cord:':<12}{GREEN}OK{RESET}")

IN = 0.0254  # metres per inch
# ---------- shared helpers (the parts that depend on each other) ----------
def fin_tab_span(fins):
    """(top, bottom) of the fin tab in inches from the nose tip, or None if no tab."""
    tab_len = fins.get_param("Tab length", "in", default=0) or 0
    if tab_len <= 0:
        return None
    top = fins.get_param("Position", "in") + fins._tab_front() / IN
    return top, top + tab_len


def motor_tube(rocket):
    """The inner tube that is set up as a motor mount (falls back to the first inner tube)."""
    tubes = rocket.components("innertube")
    mounts = [t for t in tubes if t.motor_mount is not None]
    return (mounts or tubes or [None])[0]


def check_centering_rings(rocket):
    rings = sorted(rocket.components("centeringring"), key=lambda r: r.position)
    flags = {}
    tol = 0.01  # inches

    if len(rings) != 3:                                                                                                                         #Count
        flags["Count"] = [len(rings), "centering rings found, should be 3"]
    for i, ring in enumerate(rings, 1):
        tag = f"Ring {i} ({ring.name})"
        if abs(ring.get_param("Outer diameter", "in") - 3) >= tol:                                                                              #Outer Diam
            flags[f"{tag} Outer Diameter"] = [round(ring.get_param("Outer diameter", "in"), 3), "is not the correct outer diameter, should be 3in"]
        if abs(ring.get_param("Inner diameter", "in") - 1.6) >= 0.03:   # motor tube OD is 1.62in, so allow a little more here                  #Inner Diam
            flags[f"{tag} Inner Diameter"] = [round(ring.get_param("Inner diameter", "in"), 3), "is not the correct inner diameter, should be 1.6in"]
        if abs(ring.get_param("Length", "in") - 0.25) >= tol:                                                                                   #Thickness
            flags[f"{tag} Thickness"] = [round(ring.get_param("Length", "in"), 3), "is not the correct thickness, should be 0.25in"]
        mat = ring.get_param("Material", default="none").lower()
        if not ("plywood" in mat and "birch" in mat):                                                                                           #Material
            flags[f"{tag} Material"] = [ring.get_param("Material", default="none"), "is not the correct material, should be Plywood (birch)"]

    # Location: 2 rings stacked directly above the fin tab, 1 directly below it
    fins = rocket.components("finset")
    span = fin_tab_span(fins[0]) if fins else None
    if span is None:
        flags["Location"] = ["no fin tab", "cannot check ring locations without a fin tab"]
    else:
        tab_top, tab_bot = span
        above = [r for r in rings if r.get_param("Bottom", "in") <= tab_top + tol]
        below = [r for r in rings if r.get_param("Position", "in") >= tab_bot - tol]
        if len(above) != 2:                                                                                                                     #Rings Above Tab
            flags["Rings Above Tab"] = [len(above), "rings above the fin tab, should be 2"]
        elif abs(above[1].get_param("Bottom", "in") - tab_top) >= tol:
            flags["Rings Above Tab"] = [round(tab_top - above[1].get_param("Bottom", "in"), 3), "in gap between the forward ring and the tab, should touch"]
        elif abs(above[0].get_param("Bottom", "in") - above[1].get_param("Position", "in")) >= tol:
            flags["Rings Above Tab"] = [round(above[1].get_param("Position", "in") - above[0].get_param("Bottom", "in"), 3), "in gap between the two forward rings, should be stacked"]
        if len(below) != 1:                                                                                                                     #Ring Below Tab
            flags["Ring Below Tab"] = [len(below), "rings below the fin tab, should be 1"]
        elif abs(below[0].get_param("Position", "in") - tab_bot) >= tol:
            flags["Ring Below Tab"] = [round(below[0].get_param("Position", "in") - tab_bot, 3), "in gap between the tab and the aft ring, should touch"]

    # Aft ring should sit flush with the end of the motor tube: 0.5in up from the bottom of its parent
    if rings:                                                                                                                                   #Aft Ring Position
        aft_ring = rings[-1]
        gap = aft_ring.parent.get_param("Bottom", "in") - aft_ring.get_param("Bottom", "in")
        if abs(gap - 0.5) >= tol:
            flags["Aft Ring Position"] = [round(gap, 3), f"in from the bottom of {aft_ring.parent.name}, should be 0.5in (flush with motor tube)"]

    if flags != {}:
        print(f"----- {RED}CENTERING RING FLAGS{RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Centering Rings:':<17}{GREEN}OK{RESET}")


def check_fins(rocket):
    fins = rocket.finSet
    flags = {}
    tol = 0.1  # inches

    if abs(fins.get_param("Thickness", "in") - 0.25) >= tol:                                                                                    #Thickness
        flags["Thickness"] = [round(fins.get_param("Thickness", "in"), 3), "is not the correct thickness, should be 0.25in"]
    mat = fins.get_param("Material", default="none").lower()
    if not ("plywood" in mat and "birch" in mat):                                                                                               #Material
        flags["Material"] = [fins.get_param("Material", default="none"), "is not the correct material, should be Plywood (birch)"]

    span = fin_tab_span(fins)
    if span is None:                                                                                                                            #Tab Exists
        flags["Fin Tab"] = ["none", "fins have no tab"]
    else:
        tab_top, tab_bot = span
        tab_len = tab_bot - tab_top
        if not (3 - tol <= tab_len <= 6 + tol):                                                                                                 #Tab Length
            flags["Tab Length"] = [round(tab_len, 3), "is outside the expected range, should be ~3-6in"]
        rings = rocket.components("centeringring")
        fwd = [r.get_param("Bottom", "in") for r in rings if r.get_param("Bottom", "in") <= tab_top + tol]
        aft = [r.get_param("Position", "in") for r in rings if r.get_param("Position", "in") >= tab_bot - tol]
        if not fwd or not aft:                                                                                                                  #Tab Fit
            flags["Tab Fit"] = ["no rings", "tab needs a centering ring on each end"]
        elif abs((min(aft) - max(fwd)) - tab_len) >= tol:
            flags["Tab Fit"] = [round(tab_len, 3), f"does not fit between the rings, gap is {min(aft) - max(fwd):.3f}in (script can be wrong)"]

        mt = motor_tube(rocket)
        bt = fins.parent
        if mt is None:                                                                                                                          #Tab Height
            flags["Tab Height"] = ["no motor tube", "cannot check tab height"]
        else:
            needed = bt.get_param("Outer radius", "in") - mt.get_param("Outer radius", "in")
            if abs(fins.get_param("Tab height", "in") - needed) >= tol:
                flags["Tab Height"] = [round(fins.get_param("Tab height", "in"), 3), f"does not touch the motor tube, should be {needed:.3f}in"]

    if flags != {}:
        print(f"----- {RED}FIN FLAGS{RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Fins:':<17}{GREEN}OK{RESET}")


def check_motor_tube(rocket):
    mt = motor_tube(rocket)
    flags = {}
    tol = 0.01  # inches

    if mt is None:
        print(f"----- {RED}MOTOR TUBE FLAGS{RESET} -----")
        print(f"Motor Tube: {RED}missing{RESET} no inner tube found")
        return

    if abs(mt.get_param("Inner diameter", "in") - 1.5) >= tol:                                                                                  #Inner Diam
        flags["Inner Diameter"] = [round(mt.get_param("Inner diameter", "in"), 3), "is not the correct inner diameter, should be 1.5in"]
    if abs(mt.get_param("Thickness", "in") - 0.06) >= tol:                                                                                      #Wall Thickness
        flags["Wall Thickness"] = [round(mt.get_param("Thickness", "in"), 3), "is not the correct wall thickness, should be 0.06in"]
    if abs(mt.get_param("Length", "in") - 9) >= tol:                                                                                            #Length
        flags["Length"] = [round(mt.get_param("Length", "in"), 3), "is not the correct length, should be 9in"]
    if mt.get_param("Axial method") != "bottom":                                                                                                #Position Relative To
        flags["Position Relative To"] = [mt.get_param("Axial method"), "should be positioned relative to the bottom of the body tube"]
    if abs(mt.get_param("Axial offset", "in") - (-0.5)) >= tol:                                                                                 #Plus
        flags["Position Offset"] = [round(mt.get_param("Axial offset", "in"), 3), "is not the correct offset, should be -0.5in"]
    mat = mt.get_param("Material", default="none").lower()
    if "cardboard" not in mat:                                                                                                                  #Material
        flags["Material"] = [mt.get_param("Material", default="none"), "is not the correct material, should be Cardboard"]
    if mt.motor_mount is None:                                                                                                                  #Motor Mount
        flags["Motor Mount"] = ["off", "inner tube is not set as a motor mount"]
    elif abs(mt.get_param("Overhang", "in") - 0.25) >= tol:                                                                                     #Overhang
        flags["Motor Overhang"] = [round(mt.get_param("Overhang", "in"), 3), "is not the correct overhang, should be 0.25in"]

    if flags != {}:
        print(f"----- {RED}MOTOR TUBE FLAGS{RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Motor Tube:':<17}{GREEN}OK{RESET}")

def check_motor(rocket):
    flags = {}
    motors = rocket.motors_for()   # motors in the default flight configuration

    if not motors:                                                                                                                              #Motor Present
        flags["Motor"] = ["none", "no motor loaded in the default flight configuration"]
        others = [m for m in rocket.motors if (m.designation or "").upper().startswith("H100W")]
        if others:
            flags["Motor"][1] += " (an H100W exists in another configuration, set that one as default)"
    else:
        if len(motors) != 1:                                                                                                                    #Motor Count
            flags["Motor Count"] = [len(motors), "motors loaded, should be 1"]
        m = motors[0]
        if not (m.designation or "").upper().startswith("H100W"):                                                                               #Designation
            flags["Designation"] = [m.designation, "is not the correct motor, should be H100W"]
        if "aerotech" not in (m.manufacturer or "").lower():                                                                                    #Manufacturer
            flags["Manufacturer"] = [m.manufacturer, "is not the correct manufacturer, should be AeroTech"]

    if flags != {}:
        print(f"----- {RED}MOTOR FLAGS{RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Motor:':<17}{GREEN}OK{RESET}")

def find_part(rocket, keywords, type="masscomponent"):
    """First component whose name loosely matches any keyword (mass components searched first)."""
    def norm(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())
    keys = [norm(k) for k in keywords]
    preferred = rocket.components(type)
    pool = preferred + [c for c in rocket.components() if c not in preferred]
    for c in pool:                                        # 1) keyword appears anywhere in the name
        if any(k in norm(c.name) for k in keys):
            return c
    names = {norm(c.name): c for c in preferred}          # 2) close misspellings ("eye blot")
    for k in keys:
        match = difflib.get_close_matches(k, list(names), n=1, cutoff=0.75)
        if match:
            return names[match[0]]
    return None


def span_gap(a, b):
    """Gap in inches between two parts along the rocket (0 if they overlap or touch)."""
    return max(0.0,
               a.get_param("Position", "in") - b.get_param("Bottom", "in"),
               b.get_param("Position", "in") - a.get_param("Bottom", "in"))


def check_retainer(rocket):
    ret = find_part(rocket, ["retainer", "retention", "clip"])
    flags = {}
    tol = 0.01       # inches
    mass_tol = 0.01  # grams
    place_tol = 0.5  # inches, retainer just has to be close to the ring

    if ret is None:
        print(f"----- {RED}RETAINER FLAGS{RESET} -----")
        print(f"Retainer: {RED}missing{RESET} no component with 'retainer' or 'clip' in its name")
        return

    if abs(ret.get_param("Length", "in") - 0.5) >= tol:                                                                                         #Length
        flags["Length"] = [round(ret.get_param("Length", "in"), 3), "is not the correct length, should be 0.5in"]
    if abs(ret.get_param("Outer diameter", "in") - 0.25) >= tol:                                                                                #Diameter
        flags["Diameter"] = [round(ret.get_param("Outer diameter", "in"), 3), "is not the correct diameter, should be 0.25in"]
    if abs(ret.get_param("Mass", "g") - 0.1) >= mass_tol:                                                                                       #Mass
        flags["Mass"] = [round(ret.get_param("Mass", "g"), 3), "is not the correct mass, should be 0.1g"]
    if abs(ret.get_param("Radial position", "in", default=0) - 1.15) >= tol:                                                                    #Radial Distance
        flags["Radial Distance"] = [round(ret.get_param("Radial position", "in", default=0), 3), "is not the correct radial distance, should be 1.15in"]

    rings = sorted(rocket.components("centeringring"), key=lambda r: r.position)
    if not rings:                                                                                                                               #Position
        flags["Position"] = ["no rings", "cannot check position without centering rings"]
    else:
        gap = span_gap(ret, rings[-1])
        if gap > place_tol:
            flags["Position"] = [round(gap, 3), f"in away from the bottom centering ring ({rings[-1].name}), should be on it"]

    if flags != {}:
        print(f"----- {RED}RETAINER FLAGS ({ret.name}){RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Retainer:':<17}{GREEN}OK{RESET}")


def check_eyebolt(rocket):
    eb = find_part(rocket, ["eyebolt", "eye bolt", "eye", "bolt"])
    flags = {}
    tol = 0.01        # inches
    mass_tol = 0.01   # grams
    place_tol = 0.25  # inches

    if eb is None:
        print(f"----- {RED}EYEBOLT FLAGS{RESET} -----")
        print(f"Eyebolt: {RED}missing{RESET} no component with 'eyebolt' in its name")
        return

    if abs(eb.get_param("Mass", "g") - 2.83) >= mass_tol:                                                                                       #Mass
        flags["Mass"] = [round(eb.get_param("Mass", "g"), 3), "is not the correct mass, should be 2.83g"]
    if abs(eb.get_param("Length", "in") - 1) >= tol:                                                                                            #Length
        flags["Length"] = [round(eb.get_param("Length", "in"), 3), "is not the correct length, should be 1in"]
    if abs(eb.get_param("Outer diameter", "in") - 0.25) >= tol:                                                                                 #Diameter
        flags["Diameter"] = [round(eb.get_param("Outer diameter", "in"), 3), "is not the correct diameter, should be 0.25in"]
    if abs(eb.get_param("Radial position", "in", default=0) - 1.15) >= tol:                                                                     #Radial Distance
        flags["Radial Distance"] = [round(eb.get_param("Radial position", "in", default=0), 3), "is not the correct radial distance, should be 1.15in"]

    # Should sit on the forward face of the top ring stack. Overlapping into the rings is fine.
    rings = sorted(rocket.components("centeringring"), key=lambda r: r.position)
    if not rings:                                                                                                                               #Position
        flags["Position"] = ["no rings", "cannot check position without centering rings"]
    else:
        top_ring = rings[0]
        gap = span_gap(eb, top_ring)
        if eb.get_param("Position", "in") > top_ring.get_param("Position", "in") + tol:
            flags["Position"] = [round(eb.get_param("Position", "in") - top_ring.get_param("Position", "in"), 3), f"in behind the front of {top_ring.name}, should be on top of the forward rings"]
        elif gap > place_tol:
            flags["Position"] = [round(gap, 3), f"in gap above {top_ring.name}, should be on top of the forward rings"]

    if flags != {}:
        print(f"----- {RED}EYEBOLT FLAGS ({eb.name}){RESET} -----")
        for param, val in flags.items():
            print(f"{param}: {RED}{val[0]}{RESET} {val[1]}")
    else:
        print(f"{'Eyebolt:':<17}{GREEN}OK{RESET}")


if __name__ == "__main__":
    print("")
    ork_path = "Kush Patel OR-1.ork"
    print(f"Checking: {ork_path}")
    rocket = Rocket(ork_path)
    check_materials(rocket)
    check_nosecone(rocket)
    check_body_tube(rocket)
    check_parachute(rocket)
    check_shock_cord(rocket)
    check_centering_rings(rocket)
    check_fins(rocket)
    check_motor_tube(rocket)
    check_motor(rocket)
    check_eyebolt(rocket)
    check_retainer(rocket)
    print("\n**Double check all fin-tab centering ring placement problems visually**")