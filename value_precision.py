bl_info = {
    "name": "Value Precision",
    "author": "Bradley Animation",
    "version": (0, 7, 0),
    "blender": (4, 2, 0),
    "location": "Hover a number field: press ` to type, hold middle mouse to slide",
    "description": "Type digits for exact decimals, slide values in clean steps.",
    "category": "Interface",
}

import math
import re
import struct
from decimal import Decimal
from typing import Optional, Tuple

import blf
import bpy
import gpu
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Operator
from gpu_extras.batch import batch_for_shader

try:
    from . import socket_limits  # declared ranges of built-in node inputs (generated, see tools/)
except ImportError:  # not running as part of the package
    socket_limits = None

# --------------------------------------------------------------------------- constants

# Splits a data path such as  modifiers["GeometryNodes"]["Socket_2"]  into the pointer part
# (everything before the last subscript) and the custom-property name. Geometry Nodes group
# inputs and user-defined properties are exposed this way rather than as RNA attributes.
_CUSTOM = re.compile(r'^(?P<ptr>.*?)\.?\["(?P<name>(?:[^"\\]|\\.)*)"\]$')

# Event types that type a digit (top row and numpad).
_DIGITS = {name: str(i) for i, name in enumerate(
    ('ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE'))}
_DIGITS.update({f'NUMPAD_{i}': str(i) for i in range(10)})

# Keys with a fixed meaning inside the popup; they cannot be assigned to configurable actions.
_RESERVED_KEYS = frozenset(_DIGITS) | {'MINUS', 'NUMPAD_MINUS', 'BACK_SPACE', 'SPACE', 'RET',
                                       'NUMPAD_ENTER', 'ESC'}
# Extra, always-on shift inputs in addition to the two configurable keys.
_FINER_EXTRA = frozenset({'UP_ARROW', 'WHEELUPMOUSE'})
_COARSER_EXTRA = frozenset({'DOWN_ARROW', 'WHEELDOWNMOUSE'})

_MAX_SHIFT = 9      # highest power of ten (used by sliding, which has no bar)
_TYPE_MAX_SHIFT = 5  # highest exponent on the typing popup's bar
_MAX_DIGITS = 12    # cap on typed digits
_DIV, _MUL = 'DIV', 'MUL'
_KEYMAPS = []       # (keymap, item) pairs added at register time, removed on unregister
_shader = None      # cached builtin shader; released in unregister()
# Last (mode, shift) chosen by hand this session; reused for whole-number fields (remember_last).
_last_choice: Optional[Tuple[str, int]] = None
_last_slide_choice: Optional[Tuple[str, int]] = None  # same, for sliding
_sliding = False    # True while a slide is in progress (prevents a second one starting)
_handoff = None     # field and step passed from the typing popup to the slide it starts


def key_name(identifier: str) -> str:
    """Human-readable name for an event type identifier ('ONE' -> '1')."""
    item = bpy.types.Event.bl_rna.properties['type'].enum_items.get(identifier)
    return item.name if item else identifier


# --------------------------------------------------------------------------- preferences

class VP_Preferences(AddonPreferences):
    """Addon preferences: auto-detection behaviour and the configurable keys."""

    bl_idname = __package__

    offset: IntProperty(
        name='Auto-detect Offset',
        description='Added to the number of decimals found in the current value. '
                    '0 keeps the same precision (0.05 -> 10^2), 1 goes one step finer (0.05 -> 10^3)',
        default=0, min=-3, max=6)
    whole_shift: IntProperty(
        name='Exponent for Whole Numbers',
        description='Starting ÷ 10^n when the current value has no decimals (0, 1, 10, 100 ...)',
        default=0, min=0, max=_MAX_SHIFT)
    remember_last: BoolProperty(
        name='Remember Last Choice for Whole Numbers',
        description='When a field has no decimals (for example 0), start from the mode (÷ or ×) and exponent '
                    'you last chose by hand instead of the fixed whole-number exponent',
        default=False)
    click_passthrough: BoolProperty(
        name='Click Also Acts Normally',
        description='A confirming mouse click is also passed on, so it can start the next field right away',
        default=True)
    # Stored as event-type identifiers so they survive restarts; set through VP_OT_capture_key.
    coarser_key: StringProperty(name='Coarser Key', description='Decrease the exponent (fewer decimals)', default='Z')
    finer_key: StringProperty(name='Finer Key', description='Increase the exponent (more decimals)', default='X')
    mode_key: StringProperty(name='Mode Key', description='Switch between divide and multiply', default='C')
    sign_key: StringProperty(name='Sign Key', description='Switch between positive and negative', default='V')
    all_key: StringProperty(
        name='All Axes Key',
        description='Toggle applying the change to every axis of a vector, while typing or sliding',
        default='A')
    auto_sign: BoolProperty(
        name='Detect Sign',
        description='Start typing with the sign of the current value, so a negative field gives a negative result',
        default=True)

    slide_enabled: BoolProperty(
        name='Middle Mouse Sliding',
        description='Hold the middle mouse button over a number field and drag to change it in clean steps',
        default=True)
    slide_trigger: EnumProperty(
        name='Slide Trigger',
        description='How a slide is started. Pressing the middle mouse button inside the typing popup '
                    'always starts one, whichever you choose',
        items=(('DIRECT', 'Middle Mouse over a Field',
                'Hold the middle mouse button over a number field and drag'),
               ('POPUP', 'From Popup Only',
                'Open the typing popup first, then press the middle mouse button inside it. '
                'Plain middle mouse over a field always pans')),
        default='DIRECT')
    slide_freeze_cursor: BoolProperty(
        name='Freeze Cursor While Sliding',
        description='Hide the cursor and keep it in place while sliding, like Blender\'s own number fields, '
                    'so the drag is never stopped by the edge of the screen. Turn this off if your system '
                    'cannot move the cursor (some Linux setups)',
        default=True)
    soft_limits: BoolProperty(
        name='Respect Soft Limits',
        description='Sliding stops at the soft limits of a field, like the native sliders. Typing is not '
                    'affected, and hard limits are always enforced by Blender',
        default=True)
    slide_offset: IntProperty(
        name='Slide Auto-detect Offset',
        description='Added to the number of decimals found in the current value to pick the slide step. '
                    '0 keeps the same precision (0.05 slides by 0.01). Anything above 0 adds a decimal '
                    'every time you slide again; hold Shift while sliding for a finer step instead',
        default=0, min=-3, max=6)
    slide_fine_offset: IntProperty(
        name='Extra Decimals While Holding Shift',
        description='How many decimals finer the slide step gets while Shift is held',
        default=1, min=1, max=4)
    slide_whole_shift: IntProperty(
        name='Slide Exponent for Whole Numbers',
        description='Starting step 10^-n when the current value has no decimals (0, 1, 10, 100 ...)',
        default=0, min=0, max=_MAX_SHIFT)
    slide_remember_last: BoolProperty(
        name='Remember Last Slide Step for Whole Numbers',
        description='When a field has no decimals, start sliding with the mode and exponent you last chose by hand',
        default=False)
    pixels_per_step: FloatProperty(
        name='Pixels per Step',
        description='Horizontal drag distance, in UI pixels, for one step',
        default=12.0, min=1.0, soft_max=100.0)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'offset')
        layout.prop(self, 'whole_shift')
        layout.prop(self, 'remember_last')
        layout.prop(self, 'auto_sign')
        layout.prop(self, 'click_passthrough')

        slide = layout.box()
        slide.label(text='Sliding (hold middle mouse over a field and drag)')
        slide.prop(self, 'slide_enabled')
        slide.prop(self, 'slide_trigger')
        slide.label(text='Inside the typing popup, the middle mouse button switches to sliding with the chosen step.')
        slide.prop(self, 'soft_limits')
        slide.prop(self, 'slide_offset')
        slide.prop(self, 'slide_fine_offset')
        slide.prop(self, 'slide_whole_shift')
        slide.prop(self, 'slide_remember_last')
        slide.prop(self, 'pixels_per_step')
        slide.prop(self, 'slide_freeze_cursor')
        slide.label(text='Middle mouse is bound in the "View2D", "View2D Buttons List" and "Window" keymaps.')

        box = layout.box()
        box.label(text='Keys inside the popup and while sliding (click, then press a key)')
        for attr, label in (('coarser_key', 'Coarser (smaller exponent)'),
                            ('finer_key', 'Finer (bigger exponent)'),
                            ('mode_key', 'Switch ÷ / ×'),
                            ('sign_key', 'Switch + / −'),
                            ('all_key', 'All axes (vectors)')):
            row = box.row(align=True)
            row.label(text=label)
            op = row.operator(VP_OT_capture_key.bl_idname, text=key_name(getattr(self, attr)))
            op.target = attr
        box.label(text='Mouse wheel and Up / Down arrows also change the exponent; the bar and switch work with the mouse.')

        layout.label(text='Typing: digits, Minus, Backspace.  Confirm: Space, Enter, trigger key again, or a click outside the popup.')
        layout.label(text='Cancel: Esc or right click.')
        try:
            # Show the trigger key binding with Blender's own keymap widget.
            import rna_keymap_ui
            keyconfig = context.window_manager.keyconfigs.user
            keymap = keyconfig.keymaps.get('User Interface')
            item = next(k for k in keymap.keymap_items if k.idname == VP_OT_entry.bl_idname)
            rna_keymap_ui.draw_kmi([], keyconfig, keymap, item, layout, 0)
            if item.active and item.type == 'MIDDLEMOUSE':
                # The popup uses the middle mouse button to switch to sliding, so it cannot open with it.
                warning = layout.box()
                warning.alert = True
                warning.label(text='The popup key is the middle mouse button, which switches the popup to '
                                   'sliding. Please choose another key.', icon='ERROR')
        except (AttributeError, StopIteration, ImportError):
            pass  # keymap not available (e.g. background mode); the rest of the panel still works


def _prefs(context) -> Optional[VP_Preferences]:
    """The addon's preferences, or None when it is not registered as an addon."""
    try:
        return context.preferences.addons[__package__].preferences
    except KeyError:
        return None


class VP_OT_capture_key(Operator):
    """Preferences helper: wait for the next key press and store it for one popup action."""

    bl_idname = 'value_precision.capture_key'
    bl_label = 'Set Key'
    bl_description = 'Click, then press the key to use'
    bl_options = {'INTERNAL'}

    target: StringProperty(options={'HIDDEN'})  # name of the preference to set

    def invoke(self, context, _event):
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set('Press a key (Esc to cancel)')
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # Let the window keep redrawing and tracking the mouse while we wait.
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'TIMER', 'TIMER_REPORT'}:
            return {'PASS_THROUGH'}
        if event.value != 'PRESS':
            return {'RUNNING_MODAL'}
        context.workspace.status_text_set(None)
        if event.type == 'ESC':
            return {'CANCELLED'}
        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE'} or event.type.startswith(('WHEEL', 'EVT_TWEAK')):
            return {'CANCELLED'}  # mouse buttons are not valid keys here
        if event.type in _RESERVED_KEYS:
            self.report({'WARNING'}, f'{key_name(event.type)} is used for typing or confirming')
            return {'CANCELLED'}
        prefs = _prefs(context)
        if prefs is not None:
            others = ({prefs.coarser_key, prefs.finer_key, prefs.mode_key, prefs.sign_key, prefs.all_key}
                      - {getattr(prefs, self.target)})
            if event.type in others:
                self.report({'WARNING'}, f'{key_name(event.type)} is already used by another action')
                return {'CANCELLED'}
            setattr(prefs, self.target, event.type)
        context.area.tag_redraw()
        return {'FINISHED'}


# --------------------------------------------------------------------------- value helpers

def _float32(value: float) -> float:
    """The nearest 32-bit float, which is how Blender stores most numbers."""
    return struct.unpack('<f', struct.pack('<f', value))[0]


def count_decimals(value: float) -> int:
    """Number of decimals the value was meant to have (0.05 -> 2, 33.6 -> 1, 90.0000025 -> 0).

    Blender stores most numbers as 32-bit floats, which keep only about seven significant
    digits: 33.6 is stored as 33.599998474..., so counting digits directly would find six
    decimals. Instead this looks for the fewest decimals that give back the very same stored
    number. Values that round to zero at six decimals count as 0, like a whole number.
    """
    value = abs(float(value))
    if not math.isfinite(value) or round(value, 6) == 0:
        return 0
    try:
        stored = _float32(value)
        for decimals in range(7):
            if _float32(round(value, decimals)) == stored:
                return decimals
    except OverflowError:  # larger than a 32-bit float can hold: fall back to plain digits
        pass
    return len(f'{value:.6f}'.rstrip('0').partition('.')[2])


def initial_shift(display_value: float, is_int: bool, offset: int, whole_shift: int,
                  limit: int = _MAX_SHIFT) -> int:
    """Auto-detected starting exponent (for ÷) of a field currently showing ``display_value``.

    Fields with decimals: decimals + offset. Fields without (0, 1, 10 ...): ``whole_shift``.
    Integer fields always use 0 (they start in multiply mode, see ``invoke``).
    """
    if is_int:
        return 0
    decimals = count_decimals(display_value)
    shift = decimals + offset if decimals else whole_shift
    return max(0, min(limit, shift))


def _scaled(negative: bool, digits: str, shift: int, mode: str) -> Decimal:
    """Typed digits scaled by 10**shift, as an exact Decimal (no float rounding noise)."""
    exponent = shift if mode == _MUL else -shift
    return Decimal(f"{'-' if negative else ''}{digits}e{exponent}")


def shifted_value(negative: bool, digits: str, shift: int, mode: str = _DIV) -> Optional[float]:
    """Typed digits ÷ or × 10**shift as a float, or None when nothing was typed.

    Going through Decimal means 5 with ÷10^3 is exactly 0.005, not 0.005000000000000001.
    """
    if not digits:
        return None
    return float(_scaled(negative, digits, shift, mode))


def preview_text(negative: bool, digits: str, shift: int, mode: str) -> str:
    """Exact plain-notation result for display ('0.005', '500000'), or an em dash if empty."""
    if not digits:
        return '—'
    return format(_scaled(negative, digits, shift, mode), 'f')


class Target:
    """The hovered numeric property: resolved once at invoke, written once at confirm.

    Handles regular RNA properties and ID custom properties (Geometry Nodes inputs), scalars
    and single vector components, ints and floats. Raises ``ValueError`` for anything else so
    the caller can simply pass the key press through.
    """

    def __init__(self, context, prop_tuple):
        # context.property is (owner, data_path, index); validate it before trusting it.
        if not isinstance(prop_tuple, tuple) or len(prop_tuple) != 3:
            raise ValueError('Not a numeric field')
        owner, path, index = prop_tuple
        if not isinstance(owner, bpy.types.bpy_struct) or not isinstance(path, str) or not path:
            raise ValueError('Not a numeric field')

        # Split the path into "struct that owns the value" + "property name".
        match = _CUSTOM.match(path)
        if match:
            ptr_path, self.name, self.custom = match['ptr'], bpy.utils.unescape_identifier(match['name']), True
        else:
            ptr_path, _, self.name = path.rpartition('.')
            self.custom = False
            if '[' in self.name:
                raise ValueError('Unsupported property path')
        self.pointer = owner.path_resolve(ptr_path) if ptr_path else owner
        if not isinstance(self.pointer, bpy.types.bpy_struct):
            raise ValueError('Not a numeric field')
        self.rotation = False  # True when the field is shown in degrees but stored in radians

        if self.custom:
            value = self.pointer[self.name]
            self.array = hasattr(value, 'typecode')  # IDPropertyArray exposes a typecode
            sample = value[max(0, index)] if self.array else value
            if isinstance(sample, bool) or not isinstance(sample, (int, float)):
                raise ValueError('Not a numeric field')
            self.is_int = isinstance(sample, int)
            prop = None
        else:
            prop = self.pointer.bl_rna.properties.get(self.name)
            if prop is None or prop.type not in {'FLOAT', 'INT'}:
                raise ValueError('Not a numeric field')
            if prop.is_readonly:
                raise ValueError('The field is read-only')
            self.array = prop.is_array
            self.is_int = prop.type == 'INT'
            self.rotation = (prop.type == 'FLOAT' and prop.unit == 'ROTATION'
                             and context.scene.unit_settings.system_rotation == 'DEGREES')
        if self.array and index < 0:
            raise ValueError('Hover a single component')  # whole-vector widgets (colour swatches ...)
        self.index = max(0, index)
        # "All axes" only makes sense for a flat vector (not a matrix) with several components.
        self.multi = False
        if self.array:
            try:
                # Blender pads array_dimensions to three entries: (3, 0, 0) for a vector,
                # (4, 4, 0) for a matrix. Only the non-zero entries are real dimensions.
                dims = getattr(prop, 'array_dimensions', None)
                flat = self.custom or dims is None or sum(1 for d in dims if d) <= 1
                self.multi = bool(flat and self.count() > 1)
            except (AttributeError, TypeError, ReferenceError):
                pass
        self.label = self._label(prop)
        self.soft = self._soft_limits(prop)

    def _label(self, prop) -> str:
        """A name worth showing: the socket / input name rather than 'default_value'.

        * Geometry Nodes modifier inputs are stored as 'Socket_2'; look the real name up in the
          node group's interface.
        * Node sockets expose their value as 'default_value'; use the socket's own name, with
          the node's label for context.
        * Anything else uses the property's UI name.
        """
        pointer = self.pointer
        try:
            if self.custom:
                if isinstance(pointer, bpy.types.NodesModifier) and pointer.node_group:
                    for item in pointer.node_group.interface.items_tree:
                        if item.item_type == 'SOCKET' and item.identifier == self.name:
                            return item.name
                return self.name
            name = prop.name
            owner_name = getattr(pointer, 'name', None)
            if (self.name == 'default_value' or not name) and isinstance(owner_name, str) and owner_name:
                node = getattr(pointer, 'node', None)
                node_name = (node.label or node.name) if node is not None else ''
                return f'{node_name}: {owner_name}' if node_name else owner_name
            return name or self.name.replace('_', ' ').title()
        except (AttributeError, ReferenceError, RuntimeError):
            return self.name.replace('_', ' ').title()

    def _socket_limits(self):
        """Range of a node socket's value as (low, high), or None when it is not known.

        Blender keeps the range of a socket internally and does not expose it to Python, so it
        comes from three places:

        * a group node (a custom node): the limits set on the group's interface socket, which
          Python can read;
        * an interface socket being edited in the sidebar: its own min / max;
        * a built-in node: the table generated from Blender itself (socket_limits.py). An input
          of a node that the table knows about, but that is not listed, has no limit.

        Anything else falls back to the range of the socket's type.
        """
        socket = self.pointer
        if hasattr(socket, 'min_value') and hasattr(socket, 'max_value'):
            return socket.min_value, socket.max_value  # an interface item's default value
        node = getattr(socket, 'node', None)
        if node is None:
            return None
        tree = getattr(node, 'node_tree', None)
        if tree is not None:  # group node
            for item in tree.interface.items_tree:
                if (item.item_type == 'SOCKET' and item.identifier == socket.identifier
                        and getattr(item, 'in_out', 'INPUT') in {'INPUT', 'BOTH'}):
                    return getattr(item, 'min_value', None), getattr(item, 'max_value', None)
            return None
        if socket_limits is not None and node.bl_idname in socket_limits.SCANNED:
            return socket_limits.LIMITS.get(node.bl_idname, {}).get(socket.identifier, (None, None))
        return None  # a node newer than the table: unknown

    def _soft_limits(self, prop) -> tuple:
        """(low, high) the native sliders stop at, in stored units; None means no limit.

        Sources: the UI settings of a custom property (Geometry Nodes inputs); the range of a
        node socket (see _socket_limits); otherwise the property's own soft range.
        """
        huge = 1e9  # Blender's "no limit" is +-FLT_MAX (floats) or INT_MIN / INT_MAX (ints)
        low = high = None
        try:
            if self.custom:
                ui = self.pointer.id_properties_ui(self.name).as_dict()
                low, high = ui.get('soft_min', ui.get('min')), ui.get('soft_max', ui.get('max'))
            else:
                low, high = prop.soft_min, prop.soft_max
                socket_type = getattr(bpy.types, 'NodeSocket', None)
                is_socket = socket_type is not None and isinstance(self.pointer, socket_type)
                if self.name == 'default_value' and (is_socket or hasattr(self.pointer, 'min_value')):
                    declared = self._socket_limits()
                    if declared is not None:
                        low, high = declared
                    elif (low, high) in {(-10000.0, 10000.0), (-10000, 10000)}:
                        low = high = None  # the generic range every plain socket reports, not a real limit
            low = None if low is None or abs(low) >= huge else low
            high = None if high is None or abs(high) >= huge else high
        except (AttributeError, KeyError, TypeError, ValueError, ReferenceError, RuntimeError):
            return (None, None)
        return (low, high)

    def soft_clamp(self, raw_value: float, start_raw: float) -> float:
        """Keep a slid value inside the soft limits.

        The range is widened to include ``start_raw``: a value that already sits outside the
        limits (typed there) is not pulled back, but cannot move further out, and moving back
        in works straight away.
        """
        low, high = self.soft
        if low is not None:
            raw_value = max(raw_value, min(low, start_raw))
        if high is not None:
            raw_value = min(raw_value, max(high, start_raw))
        return raw_value

    def raw(self) -> float:
        """Stored value (radians for rotations)."""
        value = self.pointer[self.name] if self.custom else getattr(self.pointer, self.name)
        return value[self.index] if self.array else value

    def display(self) -> float:
        """Value as the user sees it (degrees for rotations); used for auto-detection."""
        value = self.raw()
        return math.degrees(value) if self.rotation else value

    def write(self, display_value: float, start_raw=None) -> None:
        """Store a value given in display units (degrees for rotations).

        With ``start_raw`` the value is kept inside the soft limits first (sliding only).
        """
        value = math.radians(display_value) if self.rotation else display_value
        if start_raw is not None:
            value = self.soft_clamp(value, start_raw)
        if self.is_int:
            value = int(round(value))
        if self.array:
            values = self.pointer[self.name] if self.custom else getattr(self.pointer, self.name)
            values[self.index] = value
        elif self.custom:
            self.pointer[self.name] = value
        else:
            setattr(self.pointer, self.name, value)
        owner = self.pointer.id_data
        if owner is not None:
            owner.update_tag()  # make sure the owning ID is flagged for undo and depsgraph

    def count(self) -> int:
        """Number of components: 1 for a scalar, 3 for a location, 4 for a colour ..."""
        if not self.array:
            return 1
        return len(self.pointer[self.name] if self.custom else getattr(self.pointer, self.name))

    def to_display(self, raw_value: float) -> float:
        """Stored value -> value as shown (radians -> degrees for rotations)."""
        return math.degrees(raw_value) if self.rotation else raw_value

    def to_raw(self, display_value: float) -> float:
        """Value as shown -> stored value (degrees -> radians for rotations)."""
        return math.radians(display_value) if self.rotation else display_value

    def raw_values(self) -> list:
        """Stored values of every component (a one-item list for a scalar)."""
        value = self.pointer[self.name] if self.custom else getattr(self.pointer, self.name)
        return [value[i] for i in range(len(value))] if self.array else [value]

    def write_raw_values(self, values) -> None:
        """Store exact stored values for every component, one by one.

        Components are assigned individually (not by replacing the whole property) so custom
        properties keep their UI settings such as min / max.
        """
        if not self.array:
            self.write_raw(values[0])
            return
        array = self.pointer[self.name] if self.custom else getattr(self.pointer, self.name)
        for i, value in enumerate(values):
            array[i] = int(round(value)) if self.is_int else value
        owner = self.pointer.id_data
        if owner is not None:
            owner.update_tag()

    def write_all(self, display_value: float) -> None:
        """Store the same value (in display units) in every component."""
        self.write_raw_values([self.to_raw(display_value)] * self.count())

    def write_raw(self, raw_value: float) -> None:
        """Store an exact stored value (no unit conversion); used to restore or re-apply."""
        if self.array:
            values = self.pointer[self.name] if self.custom else getattr(self.pointer, self.name)
            values[self.index] = raw_value
        elif self.custom:
            self.pointer[self.name] = raw_value
        else:
            setattr(self.pointer, self.name, raw_value)
        owner = self.pointer.id_data
        if owner is not None:
            owner.update_tag()

    def autokey(self, context, index=None) -> None:
        """Mirror native behaviour: with Auto Keying on, only update channels already animated.

        Driven channels are skipped. Any failure is ignored on purpose: the value is already
        written and auto-keying is a convenience.
        """
        try:
            owner = self.pointer.id_data
            animation = getattr(owner, 'animation_data', None)
            if (not context.scene.tool_settings.use_keyframe_insert_auto
                    or animation is None or animation.action is None):
                return
            path = self.pointer.path_from_id('["' + bpy.utils.escape_identifier(self.name) + '"]'
                                             if self.custom else self.name)
            if index is None:
                index = self.index
            if not self.array:
                index = -1  # a scalar has a single channel
            animated = any(f.data_path == path and (index < 0 or f.array_index == index)
                           for f in animation.action.fcurves)
            if animated and not any(d.data_path == path for d in animation.drivers):
                owner.keyframe_insert(path, index=index)
        except (AttributeError, RuntimeError, TypeError, ValueError, ReferenceError):
            pass


# --------------------------------------------------------------------------- drawing

def _rgba(value) -> tuple:
    """Theme colours may be RGB or RGBA; always return RGBA."""
    color = tuple(float(c) for c in value)
    return color if len(color) == 4 else (*color[:3], 1.0)


def _mix(first: tuple, second: tuple, amount: float) -> tuple:
    """Linear blend from ``first`` to ``second`` (0 = first, 1 = second)."""
    return tuple(p + (q - p) * amount for p, q in zip(first, second))


def _path(left, bottom, width, height, radius) -> list:
    """Vertices of a (rounded) rectangle, ordered for a triangle fan."""
    radius = min(max(0.0, radius), width * .5, height * .5)
    if radius < .5:
        return [(left, bottom), (left + width, bottom), (left + width, bottom + height), (left, bottom + height)]
    steps = max(5, min(12, math.ceil(radius)))  # corner segments; enough for UI sizes
    points = []
    # Four corners: (centre x, centre y, start angle), counter-clockwise from bottom-left.
    for cx, cy, start in ((left + radius, bottom + radius, math.pi),
                          (left + width - radius, bottom + radius, math.pi * 1.5),
                          (left + width - radius, bottom + height - radius, 0.0),
                          (left + radius, bottom + height - radius, math.pi * .5)):
        for i in range(steps + 1):
            angle = start + math.pi * .5 * i / steps
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return points


def _fill(left, bottom, width, height, color, radius=0.0) -> None:
    """Draw a filled, optionally rounded rectangle in region pixel coordinates."""
    global _shader
    if width <= 0 or height <= 0 or color[3] <= 0:
        return
    if _shader is None:
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    _shader.bind()
    _shader.uniform_float('color', color)
    batch_for_shader(_shader, 'TRI_FAN', {'pos': _path(left, bottom, width, height, radius)}).draw(_shader)


def _text(text, size, rect, color, align='LEFT', pad=0.0) -> None:
    """Draw ``text`` vertically centred in ``rect``; align is 'LEFT' or 'CENTER'."""
    left, bottom, width, height = rect
    blf.size(0, size)
    text_w = blf.dimensions(0, text)[0]
    x = left + pad if align == 'LEFT' else left + (width - text_w) * .5
    # Centre on a fixed reference height so digits and letters sit on the same baseline.
    blf.position(0, x, bottom + (height - blf.dimensions(0, 'Ag')[1]) * .5, 0)
    blf.color(0, *color)
    blf.draw(0, text)


def _draw(state: dict) -> None:
    """POST_PIXEL draw callback for the popup.

    ``state`` is the operator's plain dict (not the operator itself, which may be freed while
    the handler is still registered). The card, bar and mode-switch rectangles computed here are
    stored back into ``state`` so the modal handler can hit-test mouse clicks against them.
    """
    context = bpy.context
    region = context.region
    # The handler is registered for a region *type* on every space of that class; only draw
    # in the one region the popup belongs to.
    if region is None or region.as_pointer() != state['region']:
        return

    # Theme colours and sizes are read every frame so theme and scale changes apply live.
    prefs = context.preferences
    ui = prefs.themes[0].user_interface
    menu, item = ui.wcol_menu_back, ui.wcol_menu_item
    scale = max(.25, float(prefs.system.ui_scale))
    base = float(prefs.ui_styles[0].widget.points) * scale
    big, small = base * 1.5, base * .85
    text_c, text_sel = _rgba(item.text), _rgba(item.text_sel)
    selected, inner = _rgba(item.inner_sel), _rgba(menu.inner)

    # Texts.
    shift, digits, negative, mode = state['shift'], state['digits'], state['negative'], state['mode']
    entry = ('-' if negative else '') + (digits or '_')
    operator = '×' if mode == _MUL else '÷'
    main = f'{entry}   {operator} 10^{shift}   =   {preview_text(negative, digits, shift, mode)}'
    title = state['label']
    # Coarser key first, finer key second: left to right matches the direction of the bar.
    hint_keys = (f"{state['coarser_name']} / {state['finer_name']}: exponent   "
                 f"{state['mode_name']}: ÷ ×   {state['sign_name']}: + −"
                 + (f"   {state['all_name']}: all axes" if state['multi'] else ''))
    hint_actions = 'Space: apply   MMB: slide   RMB: cancel'
    has_mode = not state['is_int']  # integer fields are always multiply

    # Layout: measure every row first so the card fits its widest text.
    pad = 10 * scale
    gap = pad * .5
    blf.size(0, base)
    all_w = blf.dimensions(0, 'All Axes')[0] + pad * 1.4  # width of the note beside the title of vector fields
    title_w = blf.dimensions(0, title)[0] + ((gap + all_w) if state['multi'] else 0)
    blf.size(0, big)
    main_w = blf.dimensions(0, main)[0]
    blf.size(0, small)
    hint_w = max(blf.dimensions(0, hint_keys)[0], blf.dimensions(0, hint_actions)[0])
    # Controls row: [÷ ×] [+ −] [0 .. 5]; the mode switch is absent for integer fields.
    groups = ([2] if has_mode else []) + [2, _TYPE_MAX_SHIFT + 1]
    cells = sum(groups)
    controls_w = base * 1.9 * cells + gap * (len(groups) - 1)
    width = max(title_w, main_w, hint_w, controls_w) + pad * 2
    heights = (small * 1.5, small * 1.5, base * 2.0, big * 1.7, base * 1.6)  # bottom -> top
    height = sum(heights) + pad
    # Sit just above-right of the cursor, clamped inside the region.
    left = min(max(state['mouse'][0] + 12 * scale, 2), max(2, region.width - width - 2))
    bottom = min(max(state['mouse'][1] + 12 * scale, 2), max(2, region.height - height - 2))
    radius = base * 1.6 * min(1.0, max(0.0, float(menu.roundness))) * .5

    gpu.state.blend_set('ALPHA')
    # Card: outline colour underneath, background inset on top.
    _fill(left, bottom, width, height, _rgba(menu.outline), radius)
    inset = max(1.0, scale)
    _fill(left + inset, bottom + inset, width - inset * 2, height - inset * 2, inner, max(0.0, radius - inset))
    state['card'] = (left, bottom, width, height)

    # Rows are laid out from the bottom up: two hint rows, controls, main line, field name.
    dim = _mix(text_c, inner, .35)
    y = bottom + pad * .5
    _text(hint_actions, small, (left, y, width, heights[0]), dim, pad=pad)
    y += heights[0]
    _text(hint_keys, small, (left, y, width, heights[1]), dim, pad=pad)
    y += heights[1]

    row_bottom, row_height = y + base * .15, heights[2] - base * .3
    cell = (width - pad * 2 - gap * (len(groups) - 1)) / cells
    track_bg = _mix(inner, _rgba(menu.outline), .35)

    def draw_switch(x, symbols, active):
        """A two-cell switch; returns its two cell rectangles for hit-testing."""
        rects = tuple((x + cell * i, row_bottom, cell, row_height) for i in range(2))
        _fill(x, row_bottom, cell * 2, row_height, track_bg, radius * .6)
        for i, (rect, symbol) in enumerate(zip(rects, symbols)):
            if i == active:
                _fill(*rect, selected, radius * .6)
            _text(symbol, base, rect, text_sel if i == active else text_c, 'CENTER')
        return rects

    track_left = left + pad
    state['mode_rects'] = None
    if has_mode:
        state['mode_rects'] = draw_switch(track_left, ('÷', '×'), 1 if mode == _MUL else 0)
        track_left += cell * 2 + gap
    state['sign_rects'] = draw_switch(track_left, ('+', '−'), 1 if negative else 0)
    track_left += cell * 2 + gap

    bar = (track_left, row_bottom, cell * (_TYPE_MAX_SHIFT + 1), row_height)
    state['slider'] = bar
    _fill(*bar, track_bg, radius * .6)
    for i in range(_TYPE_MAX_SHIFT + 1):
        rect = (bar[0] + cell * i, row_bottom, cell, row_height)
        if i == shift:
            _fill(*rect, selected, radius * .6)                 # current exponent
        elif i < shift:
            _fill(*rect, _mix(inner, selected, .35))            # tint everything below it
        _text(str(i), base, rect, text_sel if i == shift else text_c, 'CENTER')
    y += heights[2]

    _fill(left + inset * 2, y, width - inset * 4, heights[3], selected, radius * .8)
    _text(main, big, (left, y, width, heights[3]), text_sel, pad=pad)
    y += heights[3]
    _text(title, base, (left, y, width, heights[4]), text_c, pad=pad)
    state['all_rect'] = None
    if state['multi']:
        # "All Axes" note at the right end of the title row (lit when on): type once, set every axis.
        rect = (left + width - pad - all_w, y + base * .15, all_w, heights[4] - base * .3)
        state['all_rect'] = rect
        on = state['all_axes']
        _fill(*rect, selected if on else track_bg, radius * .6)
        _text('All Axes', base, rect, text_sel if on else text_c, 'CENTER')
    gpu.state.blend_set('NONE')


# --------------------------------------------------------------------------- operator

class VP_OT_entry(Operator):
    """The popup itself: invoke on the hovered field, collect input modally, apply once."""

    bl_idname = 'value_precision.entry'
    bl_label = 'Value Precision Entry'
    bl_description = 'Type digits that are divided or multiplied by 10^n for the hovered number field'
    bl_options = {'UNDO', 'INTERNAL'}  # UNDO: the confirmed write becomes one undo step

    def invoke(self, context, event):
        # Not over a usable field -> do nothing and let the key keep its normal meaning.
        prop_tuple = getattr(context, 'property', None)
        if prop_tuple is None or context.space_data is None or context.region is None:
            return {'PASS_THROUGH'}
        if event.type == 'MIDDLEMOUSE':
            # Inside the popup this button switches to sliding, so it cannot also open the popup.
            self.report({'WARNING'}, 'The popup key cannot be the middle mouse button. '
                                     'Change it in the add-on preferences')
            return {'PASS_THROUGH'}
        try:
            self._target = Target(context, prop_tuple)
            display = self._target.display()
        except (ValueError, KeyError, AttributeError, TypeError, ReferenceError, RuntimeError):
            return {'PASS_THROUGH'}

        prefs = _prefs(context)
        offset = prefs.offset if prefs else 0
        whole = prefs.whole_shift if prefs else 0
        remember = prefs.remember_last if prefs else True
        self._passthrough = prefs.click_passthrough if prefs else True
        self._trigger = event.type  # pressing the same key again confirms (unless it has another job)
        coarser = prefs.coarser_key if prefs else 'Z'
        finer = prefs.finer_key if prefs else 'X'
        mode_key = prefs.mode_key if prefs else 'C'
        self._finer = frozenset({finer}) | _FINER_EXTRA
        self._coarser = frozenset({coarser}) | _COARSER_EXTRA
        self._mode_key = mode_key
        sign_key = prefs.sign_key if prefs else 'V'
        self._sign_key = sign_key
        all_key = prefs.all_key if prefs else 'A'
        self._all_key = all_key
        # The result takes the sign of the current value (zero counts as positive).
        negative = bool(prefs.auto_sign if prefs else True) and display < 0

        # Starting mode and exponent. Integers start in multiply mode (dividing them is
        # pointless); floats with decimals are auto-detected; whole-number floats may reuse the
        # last manual choice.
        if self._target.is_int:
            mode, shift = _MUL, 0
        else:
            mode, shift = _DIV, initial_shift(display, False, offset, whole, _TYPE_MAX_SHIFT)
            if not count_decimals(display) and remember and _last_choice is not None:
                mode, shift = _last_choice
                shift = min(shift, _TYPE_MAX_SHIFT)

        # Everything the draw callback and modal need lives in a plain dict, so the draw
        # handler never holds a reference to the operator instance.
        self._state = {
            'region': context.region.as_pointer(),
            'mouse': (event.mouse_region_x, event.mouse_region_y),
            'label': self._target.label,
            'shift': shift,
            'mode': mode,
            'is_int': self._target.is_int,
            'digits': '',
            'negative': negative,
            'sign_rects': None,    # ((+ rect), (− rect)), set by _draw
            'manual': False,       # True once the user changed mode or exponent by hand
            'dragging': False,     # True while the mouse is scrubbing the bar
            'slider': None,        # (left, bottom, width, height) of the bar, set by _draw
            'mode_rects': None,    # ((÷ rect), (× rect)), set by _draw
            'card': None,          # rectangle of the whole popup
            'coarser_name': key_name(coarser),
            'finer_name': key_name(finer),
            'mode_name': key_name(mode_key),
            'sign_name': key_name(sign_key),
            'multi': self._target.multi,   # hovered field is one axis of a vector
            'all_axes': False,             # apply the typed value to every axis
            'all_rect': None,              # rectangle of the "All" toggle, set by _draw
            'all_name': key_name(all_key),
        }
        self._space = type(context.space_data)
        self._region_type = context.region.type
        self._area, self._region = context.area, context.region  # handed to a slide started from here
        self._handle = self._space.draw_handler_add(_draw, (self._state,), self._region_type, 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        context.region.tag_redraw()
        return {'RUNNING_MODAL'}

    # ---- helpers -----------------------------------------------------------------

    def _cleanup(self, context) -> None:
        """Remove the draw handler and refresh the screen. Safe to call more than once."""
        handle = getattr(self, '_handle', None)
        if handle is not None:
            self._space.draw_handler_remove(handle, self._region_type)
            self._handle = None
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()

    def cancel(self, context):
        """Called by Blender when the modal is aborted from outside (e.g. file load)."""
        self._cleanup(context)

    @staticmethod
    def _inside(rect, x, y) -> bool:
        return bool(rect) and rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]

    @staticmethod
    def _cell(state, x) -> int:
        """Exponent under region x coordinate ``x`` on the bar (clamped to 0..9)."""
        left, _bottom, width, _height = state['slider']
        return max(0, min(_TYPE_MAX_SHIFT, int((x - left) / (width / (_TYPE_MAX_SHIFT + 1)))))

    def _set_shift(self, context, value: int) -> None:
        """Change the exponent by hand and remember that it was a manual override."""
        self._state['shift'] = value
        self._state['manual'] = True
        context.region.tag_redraw()

    def _set_mode(self, context, mode: str) -> None:
        """Switch between divide and multiply (not available for integer fields)."""
        if self._state['is_int']:
            return
        self._state['mode'] = mode
        self._state['manual'] = True
        context.region.tag_redraw()

    def _to_slide(self, context, event):
        """Middle mouse inside the popup: apply what was typed, then continue as a slide.

        The slide starts from the field's new value with the exponent, divide / multiply and
        all-axes settings shown in the popup. If nothing was typed the field is left untouched.
        The typed value is its own undo step, and cancelling the slide returns to that value.
        """
        global _handoff
        prefs = _prefs(context)
        if prefs is not None and not prefs.slide_enabled:
            return {'RUNNING_MODAL'}  # sliding is switched off: ignore the button
        state = self._state
        self._cleanup(context)  # closes the popup
        typed = bool(state['digits'])
        result = self._commit(context)  # applies the typed value, if any
        if typed and 'FINISHED' not in result:
            return result  # the write failed; do not start a slide on a field in an unknown state
        _handoff = {
            'target': self._target, 'area': self._area, 'region': self._region,
            'mouse': (event.mouse_x, event.mouse_y),
            'mode': state['mode'], 'shift': state['shift'],
            'all_axes': state['all_axes'], 'manual': state['manual'],
            'card': state['card'],  # where the popup was drawn, so the slide panel can stay there
        }
        try:
            bpy.ops.value_precision.slide('INVOKE_DEFAULT')
        except RuntimeError:
            pass  # the slide could not start; the popup is closed
        finally:
            _handoff = None  # never leave a stale handoff behind
        return {'FINISHED'} if typed else {'CANCELLED'}

    def _confirm(self, context):
        """Close the popup and apply the typed value. Returns the operator result set."""
        self._cleanup(context)
        return self._commit(context)

    def _commit(self, context):
        """Write the typed value, if any, to the field. Returns the operator result set."""
        global _last_choice
        state = self._state
        # Remember a manual choice only when a value was actually typed with it.
        if state['manual'] and not state['is_int'] and state['digits']:
            _last_choice = (state['mode'], state['shift'])
        value = shifted_value(state['negative'], state['digits'], state['shift'], state['mode'])
        if value is None:
            return {'CANCELLED'}  # nothing typed: leave the field untouched
        try:
            if state['all_axes'] and state['multi']:
                self._target.write_all(value)
                for axis in range(self._target.count()):
                    self._target.autokey(context, axis)
            else:
                self._target.write(value)
                self._target.autokey(context)
        except (ReferenceError, AttributeError, TypeError, ValueError, RuntimeError, KeyError):
            self.report({'ERROR'}, 'The field changed or became invalid')
            return {'CANCELLED'}
        return {'FINISHED'}

    # ---- event handling ----------------------------------------------------------

    def modal(self, context, event):
        state = self._state
        kind = event.type
        x, y = event.mouse_region_x, event.mouse_region_y
        moving = kind in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}

        # 0. Middle mouse switches to sliding from the same field.
        if kind == 'MIDDLEMOUSE' and event.value == 'PRESS':
            return self._to_slide(context, event)

        # 1. Scrubbing the bar takes priority over everything else.
        if state['dragging']:
            if moving:
                self._set_shift(context, self._cell(state, x))
                return {'RUNNING_MODAL'}
            if kind == 'LEFTMOUSE' and event.value == 'RELEASE':
                state['dragging'] = False
                return {'RUNNING_MODAL'}

        # 2. Plain mouse movement and timers pass through so the UI stays responsive.
        if moving or kind in {'TIMER', 'TIMER_REPORT'}:
            return {'PASS_THROUGH'}

        # 3. Clicks: the ÷ / × switch, then the bar (starts scrubbing), then the rest of the
        #    popup (ignored). A click outside the popup confirms (handled further down).
        if kind == 'LEFTMOUSE' and event.value == 'PRESS':
            if state['mode_rects']:
                for rect, mode in zip(state['mode_rects'], (_DIV, _MUL)):
                    if self._inside(rect, x, y):
                        self._set_mode(context, mode)
                        return {'RUNNING_MODAL'}
            if state['all_rect'] and self._inside(state['all_rect'], x, y):
                state['all_axes'] = not state['all_axes']
                context.region.tag_redraw()
                return {'RUNNING_MODAL'}
            if state['sign_rects']:
                for rect, negative in zip(state['sign_rects'], (False, True)):
                    if self._inside(rect, x, y):
                        state['negative'] = negative
                        context.region.tag_redraw()
                        return {'RUNNING_MODAL'}
            if state['slider'] and self._inside(state['slider'], x, y):
                state['dragging'] = True
                self._set_shift(context, self._cell(state, x))
                return {'RUNNING_MODAL'}
            if self._inside(state['card'], x, y):
                return {'RUNNING_MODAL'}  # clicking the popup itself never confirms

        # Everything below reacts to presses only; releases are swallowed.
        if event.value != 'PRESS':
            return {'RUNNING_MODAL'}

        # 4. Cancel.
        if kind in {'ESC', 'RIGHTMOUSE'}:
            self._cleanup(context)
            return {'CANCELLED'}

        # 5. Confirm. The trigger key confirms too, except when it is also assigned to the
        #    exponent or mode (e.g. trigger = Z with Z as "coarser"): then that job wins.
        busy = (kind in self._coarser or kind in self._finer
                or kind in {self._mode_key, self._sign_key, self._all_key})
        if kind in {'SPACE', 'RET', 'NUMPAD_ENTER'} or (kind == self._trigger and not busy
                                                        and not event.is_repeat):
            return self._confirm(context)
        if kind == 'LEFTMOUSE':  # a click outside the popup
            result = self._confirm(context)
            if self._passthrough and 'FINISHED' in result:
                return {'FINISHED', 'PASS_THROUGH'}  # also let the click do its normal job
            return result

        # 6. Typing, exponent and mode.
        if kind in _DIGITS:
            if len(state['digits']) < _MAX_DIGITS:
                state['digits'] += _DIGITS[kind]
        elif kind in {'MINUS', 'NUMPAD_MINUS'} or kind == self._sign_key:
            state['negative'] = not state['negative']
        elif kind == 'BACK_SPACE':
            state['digits'] = state['digits'][:-1]
        elif kind in self._coarser:
            self._set_shift(context, max(0, state['shift'] - 1))
        elif kind in self._finer:
            self._set_shift(context, min(_TYPE_MAX_SHIFT, state['shift'] + 1))
        elif kind == self._mode_key:
            self._set_mode(context, _DIV if state['mode'] == _MUL else _MUL)
        elif kind == self._all_key and state['multi']:
            state['all_axes'] = not state['all_axes']
        else:
            return {'RUNNING_MODAL'}  # unknown key: swallow it so nothing leaks to the editor
        context.region.tag_redraw()
        return {'RUNNING_MODAL'}


# --------------------------------------------------------------------------- sliding

def step_size(shift: int, mode: str) -> Decimal:
    """Size of one slide step: 10^-shift in divide mode, 10^shift in multiply mode (exact)."""
    return Decimal(1).scaleb(shift if mode == _MUL else -shift)


def slide_value(original: float, step: Decimal, ticks: int) -> Optional[float]:
    """Value after dragging ``ticks`` whole steps away from ``original``, on the step grid.

    * ``ticks == 0`` returns None: the value is left exactly as it was.
    * If ``original`` already sits on the grid, the result is original + ticks * step.
    * If it does not (0.0537 with step 0.001), the first tick in each direction lands on the
      nearest grid value in that direction (0.054 up, 0.053 down) and the rest follow.

    Whole-grid indices times an exact Decimal step avoid float noise like 0.30000000000000004.
    """
    if ticks == 0:
        return None
    ratio = original / float(step)
    nearest = round(ratio)
    tolerance = max(1e-3, abs(ratio) * 1e-6)  # float32 storage is slightly off-grid
    if abs(ratio - nearest) <= tolerance:
        index = nearest + ticks
    elif ticks > 0:
        index = math.ceil(ratio) + ticks - 1
    else:
        index = math.floor(ratio) + ticks + 1
    return float(Decimal(index) * step)


def _region_at(window, point):
    """The (area, region) under window coordinates ``point``.

    Keymap handlers run in the window's context, not the hovered region's, so the region is
    looked up by position. Narrow regions (header, sidebar) are checked before the main one.
    """
    for area in window.screen.areas:
        for region in sorted(area.regions, key=lambda r: r.type == 'WINDOW'):
            if region.type not in {'WINDOW', 'UI', 'HEADER', 'TOOL_HEADER'} or region.width < 2:
                continue
            if (region.x <= point[0] < region.x + region.width
                    and region.y <= point[1] < region.y + region.height):
                return area, region
    return None, None


def _draw_slide(state: dict) -> None:
    """POST_PIXEL draw callback for the slide HUD (field name, live value, current step)."""
    context = bpy.context
    region = context.region
    if region is None or region.as_pointer() != state['region']:
        return
    prefs = context.preferences
    ui = prefs.themes[0].user_interface
    menu, item = ui.wcol_menu_back, ui.wcol_menu_item
    scale = max(.25, float(prefs.system.ui_scale))
    base = float(prefs.ui_styles[0].widget.points) * scale
    big, small = base * 1.5, base * .85
    text_c, text_sel = _rgba(item.text), _rgba(item.text_sel)
    selected, inner = _rgba(item.inner_sel), _rgba(menu.inner)

    operator = '×' if state['mode'] == _MUL else '÷'
    step_text = format(step_size(state['eff_shift'], state['mode']), 'f')
    title = state['label']
    main = state['value_text']
    step_line = f"step   {operator} 10^{state['eff_shift']}   =   {step_text}"
    hint = (f"{state['coarser_name']} / {state['finer_name']}: step   {state['mode_name']}: ÷ ×   "
            + (f"{state['all_name']}: all axes   " if state['multi'] else '')
            + "Shift: finer   RMB: cancel")

    pad = 10 * scale
    blf.size(0, base)
    all_w = blf.dimensions(0, 'All Axes')[0] + pad * 1.4  # the same note as in the typing popup
    widths = [blf.dimensions(0, title)[0] + ((pad * .5 + all_w) if state['multi'] else 0),
              blf.dimensions(0, step_line)[0]]
    blf.size(0, big)
    widths.append(blf.dimensions(0, main)[0])
    blf.size(0, small)
    widths.append(blf.dimensions(0, hint)[0])
    width = max(widths) + pad * 2
    heights = (small * 1.7, base * 1.7, big * 1.7, base * 1.6)  # bottom -> top
    height = sum(heights) + pad
    if state['top_left'] is not None:
        # Continuing from the typing popup: its title and value rows have the same heights as
        # these, so keeping the top-left corner leaves them exactly where they were.
        left, bottom = state['top_left'][0], state['top_left'][1] - height
    else:
        left, bottom = state['mouse'][0] + 12 * scale, state['mouse'][1] + 12 * scale
    left = min(max(left, 2), max(2, region.width - width - 2))
    bottom = min(max(bottom, 2), max(2, region.height - height - 2))
    radius = base * 1.6 * min(1.0, max(0.0, float(menu.roundness))) * .5

    gpu.state.blend_set('ALPHA')
    _fill(left, bottom, width, height, _rgba(menu.outline), radius)
    inset = max(1.0, scale)
    _fill(left + inset, bottom + inset, width - inset * 2, height - inset * 2, inner, max(0.0, radius - inset))
    y = bottom + pad * .5
    _text(hint, small, (left, y, width, heights[0]), _mix(text_c, inner, .35), pad=pad)
    y += heights[0]
    _text(step_line, base, (left, y, width, heights[1]), text_c, pad=pad)
    y += heights[1]
    _fill(left + inset * 2, y, width - inset * 4, heights[2], selected, radius * .8)
    _text(main, big, (left, y, width, heights[2]), text_sel, pad=pad)
    y += heights[2]
    _text(title, base, (left, y, width, heights[3]), text_c, pad=pad)
    if state['multi']:
        # Same note as in the typing popup: top right, lit while all axes move together.
        rect = (left + width - pad - all_w, y + base * .15, all_w, heights[3] - base * .3)
        on = state['all_axes']
        _fill(*rect, selected if on else _mix(inner, _rgba(menu.outline), .35), radius * .6)
        _text('All Axes', base, rect, text_sel if on else text_c, 'CENTER')
    gpu.state.blend_set('NONE')


_SLIDE_ERRORS = (ReferenceError, AttributeError, TypeError, ValueError, RuntimeError, KeyError)


class VP_OT_slide(Operator):
    """Middle-mouse sliding: write live while dragging, restore on cancel, commit on release."""

    bl_idname = 'value_precision.slide'
    bl_label = 'Value Precision Slide'
    bl_description = 'Hold middle mouse over a number field and drag to change it in clean steps'
    bl_options = {'UNDO', 'INTERNAL'}  # one undo step, pushed only when the slide is committed

    def invoke(self, context, event):
        global _sliding, _handoff
        window = context.window
        handoff, _handoff = _handoff, None  # always consumed, so a stale one never leaks into a later press
        if _sliding or window is None:
            return {'PASS_THROUGH'}
        prefs = _prefs(context)
        if prefs is not None and not prefs.slide_enabled:
            return {'PASS_THROUGH'}

        if handoff is not None:
            # Started from the typing popup: the field, region and step are already known.
            self._target = handoff['target']
            area, region = handoff['area'], handoff['region']
            start_x, start_y = handoff['mouse']
            try:
                display = self._target.display()
                original_raw = self._target.raw()
                original_all = self._target.raw_values()
            except _SLIDE_ERRORS:
                return {'PASS_THROUGH'}
        else:
            # Direct: plain middle mouse over a field, unless sliding is popup-only.
            if event.type != 'MIDDLEMOUSE' or event.value != 'PRESS':
                return {'PASS_THROUGH'}
            if prefs is not None and prefs.slide_trigger != 'DIRECT':
                return {'PASS_THROUGH'}
            start_x, start_y = event.mouse_x, event.mouse_y
            area, region = _region_at(window, (start_x, start_y))
            if area is None:
                return {'PASS_THROUGH'}
            try:
                # The hovered button is only visible from inside its own region.
                with context.temp_override(window=window, area=area, region=region):
                    if getattr(bpy.context, 'region_popup', None) is not None:
                        return {'PASS_THROUGH'}
                    self._target = Target(bpy.context, getattr(bpy.context, 'property', None))
                    display = self._target.display()
                    original_raw = self._target.raw()
                    original_all = self._target.raw_values()
            except _SLIDE_ERRORS:
                return {'PASS_THROUGH'}  # not a numeric field: native middle-mouse (panning) continues

        offset = prefs.slide_offset if prefs else 0
        whole = prefs.slide_whole_shift if prefs else 0
        remember = prefs.slide_remember_last if prefs else True
        coarser = prefs.coarser_key if prefs else 'Z'
        finer = prefs.finer_key if prefs else 'X'
        mode_key = prefs.mode_key if prefs else 'C'
        self._coarser = frozenset({coarser}) | _COARSER_EXTRA
        self._finer = frozenset({finer}) | _FINER_EXTRA
        self._mode_key = mode_key
        all_key = prefs.all_key if prefs else 'A'
        self._all_key = all_key
        pixels = prefs.pixels_per_step if prefs else 12.0
        self._pixels = max(1.0, pixels) * max(.25, float(context.preferences.system.ui_scale))
        self._soft_on = bool(prefs.soft_limits) if prefs else True

        if handoff is not None:
            # Carry over what the popup showed: exponent, divide / multiply and all-axes.
            mode, shift = handoff['mode'], min(handoff['shift'], _MAX_SHIFT)
            all_axes = bool(handoff['all_axes']) and self._target.multi
            manual = bool(handoff['manual'])
        else:
            # Same starting-step logic as typing, with its own preferences and memory.
            all_axes = manual = False
            if self._target.is_int:
                mode, shift = _MUL, 0
            else:
                mode, shift = _DIV, initial_shift(display, False, offset, whole)
                if not count_decimals(display) and remember and _last_slide_choice is not None:
                    mode, shift = _last_slide_choice

        self._original_all = original_all           # every axis as it was (restored on cancel)
        self._expected_all = list(original_all)     # what we last wrote (for the re-apply timer)
        self._base = display                # value the current drag distance is measured from
        self._base_raw = original_raw       # the same value in stored units (exact)
        self._original_display = display            # hovered axis as it was, in display units
        self._window = window
        self._freeze = bool(prefs.slide_freeze_cursor) if prefs else True
        self._center = (start_x, start_y)   # where the cursor is held while it is frozen
        self._virtual_x = float(start_x)    # horizontal position as if the cursor could move freely
        self._anchor_x = self._virtual_x    # where the current measurement starts
        self._ticks = 0
        self._state = {
            'region': region.as_pointer(),
            'mouse': (start_x - region.x, start_y - region.y),
            # Started from the popup: keep its top-left corner so the panel does not jump.
            'top_left': ((handoff['card'][0], handoff['card'][1] + handoff['card'][3])
                         if handoff is not None and handoff.get('card') else None),
            'label': self._target.label,
            'shift': shift,                 # step exponent chosen by detection / keys
            'eff_shift': shift,             # exponent actually used (adds the Shift-held bonus)
            'fine': False,                  # True while Shift is held
            'fine_extra': prefs.slide_fine_offset if prefs else 1,
            'mode': mode,
            'is_int': self._target.is_int,
            'manual': manual,
            'value_text': '',
            'coarser_name': key_name(coarser),
            'finer_name': key_name(finer),
            'mode_name': key_name(mode_key),
            'multi': self._target.multi,    # hovered field is one axis of a vector
            'all_axes': all_axes,           # move every axis together
            'all_name': key_name(all_key),
        }
        self._refresh_text()
        self._space = type(area.spaces.active)
        self._region_type = region.type
        self._handle = self._space.draw_handler_add(_draw_slide, (self._state,), self._region_type, 'POST_PIXEL')
        # Timer: re-apply our value while sliding, as animation evaluation can overwrite it.
        self._timer = context.window_manager.event_timer_add(.1, window=window)
        _sliding = True
        context.window_manager.modal_handler_add(self)
        if self._freeze:
            window.cursor_modal_set('NONE')  # hidden and held in place, like a native number field
        region.tag_redraw()
        return {'RUNNING_MODAL'}

    # ---- helpers -----------------------------------------------------------------

    def _refresh_text(self) -> None:
        """Format the live value for the HUD (never fewer decimals than the step shows)."""
        state = self._state
        try:
            value = self._target.display()
            decimals = max(0 if state['mode'] == _MUL else state['eff_shift'], count_decimals(value))
            state['value_text'] = f'{value:.{decimals}f}'
        except _SLIDE_ERRORS:
            pass

    def _cleanup(self, context) -> None:
        """Remove the handler and timer and refresh the screen. Safe to call twice."""
        global _sliding
        _sliding = False
        handle = getattr(self, '_handle', None)
        if handle is not None:
            self._space.draw_handler_remove(handle, self._region_type)
            self._handle = None
        timer = getattr(self, '_timer', None)
        if timer is not None:
            context.window_manager.event_timer_remove(timer)
            self._timer = None
        if getattr(self, '_freeze', False):
            self._freeze = False
            try:
                self._window.cursor_warp(*self._center)  # the cursor reappears where the slide began
                self._window.cursor_modal_restore()
            except (RuntimeError, ReferenceError, AttributeError):
                pass
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()

    def _restore(self) -> None:
        """Put the original value back (cancel path); failures are ignored on purpose."""
        try:
            self._target.write_raw_values(self._original_all)
        except _SLIDE_ERRORS:
            pass

    def cancel(self, context):
        """Called by Blender if the modal is aborted from outside (file load, window closed)."""
        self._restore()
        self._cleanup(context)

    def _update_effective(self) -> None:
        """Exponent in use: the chosen one, plus the Shift bonus (a smaller step) while held.

        The bonus is temporary and never stored, so it cannot accumulate between slides.
        """
        state = self._state
        extra = state['fine_extra'] if state['fine'] and not state['is_int'] else 0
        # Finer means a smaller step: a bigger exponent when dividing, a smaller one when multiplying.
        shift = state['shift'] + extra if state['mode'] == _DIV else state['shift'] - extra
        state['eff_shift'] = max(0, min(_MAX_SHIFT, shift))

    def _reanchor(self, context, event) -> None:
        """Continue the drag from the current value, so a step change never makes it jump."""
        self._base = self._target.display()
        self._base_raw = self._target.raw()
        self._expected_all = self._target.raw_values()
        self._anchor_x = self._virtual_x
        self._ticks = 0
        self._refresh_text()
        self._redraw(context)

    def _set_step(self, context, event, *, shift=None, mode=None) -> None:
        """Change the step mid-drag; the drag continues from the current value at the new step."""
        state = self._state
        if mode is not None:
            state['mode'] = mode
        if shift is not None:
            state['shift'] = shift
        state['manual'] = True
        self._update_effective()
        self._reanchor(context, event)

    def _follow(self) -> None:
        """Make every other axis follow the hovered axis' total change since the drag began.

        Each other axis = its original value + (hovered now - hovered originally). Axes that
        started apart stay apart by the same amount; axes that started equal stay equal. Using
        the total change, not the change since A was pressed, means pressing A after sliding
        one axis brings the others along at once.
        """
        target = self._target
        delta = target.display() - self._original_display
        current = target.raw_values()

        def limited(axis, raw):  # every axis stops at its own soft limits
            return target.soft_clamp(raw, self._original_all[axis]) if self._soft_on else raw

        target.write_raw_values([
            current[axis] if axis == target.index else limited(axis, target.to_raw(target.to_display(raw) + delta))
            for axis, raw in enumerate(self._original_all)])

    def _write_drag(self, value) -> bool:
        """Write one drag result: ``value`` is the hovered axis in display units, None = back at the anchor.

        Returns True when a soft limit stopped the hovered axis.
        """
        target = self._target
        clamped = False
        if value is None:
            target.write_raw(self._base_raw)    # back at the anchor: exactly as it was
        else:
            start = self._original_all[target.index]
            if self._soft_on:
                raw = target.to_raw(value)
                clamped = target.soft_clamp(raw, start) != raw
            target.write(value, start_raw=start if self._soft_on else None)
        if self._state['all_axes'] and target.multi:
            self._follow()
        return clamped

    def _track_mouse(self, event) -> None:
        """Update the virtual horizontal position from a mouse move.

        With the cursor frozen, every move is measured from the point where the slide began and
        the cursor is put back there, so the drag can go on forever. Putting it back causes one
        more move event at that point, which adds nothing. Without freezing, the real position
        is used and the drag stops at the edge of the screen.
        """
        if not self._freeze:
            self._virtual_x = float(event.mouse_x)
            return
        center_x, center_y = self._center
        moved_x, moved_y = event.mouse_x - center_x, event.mouse_y - center_y
        if moved_x or moved_y:
            self._virtual_x += moved_x
            self._window.cursor_warp(center_x, center_y)

    def _apply_drag(self, context, event) -> None:
        """Write the value for the current horizontal distance (whole steps only)."""
        ticks = math.trunc((self._virtual_x - self._anchor_x) / self._pixels)
        if ticks == self._ticks:
            return
        self._ticks = ticks
        step = step_size(self._state['eff_shift'], self._state['mode'])
        value = slide_value(self._base, step, ticks)
        clamped = self._write_drag(value)
        self._expected_all = self._target.raw_values()  # read back: includes clamping / float32
        self._refresh_text()
        self._redraw(context)
        if clamped:
            # Stopped at a soft limit: measure from here, so reversing moves the value straight away.
            self._reanchor(context, event)

    @staticmethod
    def _redraw(context) -> None:
        """The HUD may live in a different area than the one under the cursor; refresh all."""
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()

    # ---- event handling ----------------------------------------------------------

    def modal(self, context, event):
        global _last_slide_choice
        state = self._state
        kind = event.type
        try:
            if kind == 'WINDOW_DEACTIVATE' or (kind in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS'):
                self._restore()
                self._cleanup(context)
                return {'CANCELLED'}

            if kind == 'MIDDLEMOUSE' and event.value == 'RELEASE':
                now = self._target.raw_values()
                changed = now != self._original_all
                if changed:
                    # Keyframe each changed axis once, like a native edit.
                    for axis, (before, after) in enumerate(zip(self._original_all, now)):
                        if before != after:
                            self._target.autokey(context, axis)
                    if state['manual'] and not state['is_int']:
                        _last_slide_choice = (state['mode'], state['shift'])
                self._cleanup(context)
                return {'FINISHED'} if changed else {'CANCELLED'}

            # Holding Shift makes the step one decimal finer; every event carries the key state,
            # so pressing or releasing it mid-drag is picked up immediately.
            if event.shift != state['fine']:
                state['fine'] = event.shift
                self._update_effective()
                self._reanchor(context, event)

            if kind in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                self._track_mouse(event)
                self._apply_drag(context, event)
            elif kind == 'TIMER':
                if self._target.raw_values() != self._expected_all:
                    self._target.write_raw_values(self._expected_all)
            elif event.value == 'PRESS':
                if kind in self._coarser:
                    self._set_step(context, event, shift=max(0, state['shift'] - 1))
                elif kind in self._finer:
                    self._set_step(context, event, shift=min(_MAX_SHIFT, state['shift'] + 1))
                elif kind == self._mode_key and not state['is_int']:
                    self._set_step(context, event, mode=_DIV if state['mode'] == _MUL else _MUL)
                elif kind == self._all_key and state['multi']:
                    # Toggle moving all axes. Switching on brings the other axes along right away;
                    # switching off leaves them where they are.
                    state['all_axes'] = not state['all_axes']
                    if state['all_axes']:
                        self._follow()
                    self._reanchor(context, event)
        except _SLIDE_ERRORS:
            self.report({'WARNING'}, 'The field changed during the slide; the original value was restored')
            self._restore()
            self._cleanup(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


# --------------------------------------------------------------------------- registration

_CLASSES = (VP_Preferences, VP_OT_capture_key, VP_OT_entry, VP_OT_slide)
_registered = []  # classes that really registered, so unregister never touches the others


def register():
    try:
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
            _registered.append(cls)
        keyconfig = bpy.context.window_manager.keyconfigs.addon
        if keyconfig is None:  # background mode has no key configuration
            return
        # "User Interface" is consulted when the pointer is over a button, which is what makes
        # context.property available to invoke(); the default trigger is the grave accent key (`).
        keymap = keyconfig.keymaps.new('User Interface', space_type='EMPTY', region_type='WINDOW')
        item = keymap.keymap_items.new(VP_OT_entry.bl_idname, 'ACCENT_GRAVE', 'PRESS')
        _KEYMAPS.append((keymap, item))
        # Middle mouse: View2D handles it first for panning (sidebars, Properties, node editor), so
        # the slide entry goes to the head of those keymaps. Anything that is not a numeric field
        # returns PASS_THROUGH and native panning continues.
        for name in ('View2D Buttons List', 'View2D', 'Window'):
            keymap = keyconfig.keymaps.new(name=name, space_type='EMPTY')
            item = keymap.keymap_items.new(VP_OT_slide.bl_idname, 'MIDDLEMOUSE', 'PRESS', head=True)
            _KEYMAPS.append((keymap, item))
    except Exception:
        unregister()  # roll back whatever did register, then let the original error through
        raise


def unregister():
    global _shader
    for keymap, item in _KEYMAPS:
        try:
            keymap.keymap_items.remove(item)
        except (RuntimeError, ReferenceError):
            pass  # already gone, e.g. the key configuration was reloaded
    _KEYMAPS.clear()
    for cls in reversed(_registered):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass  # not registered any more
    _registered.clear()
    _shader = None  # drop the cached GPU shader wrapper
