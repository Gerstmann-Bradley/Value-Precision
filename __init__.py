import bpy
from . import value_precision

def register():
    value_precision.register()


def unregister():
    value_precision.unregister()
