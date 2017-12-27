"""
Mock unicornhathd module.

The intention is to use this module when developing on a different computer than a Raspberry PI with the UnicornHAT
attached, e.g. on a Mac laptop. In such case, this module will simply provide the same functions as the original
uncornhathd module, however most of them will do absolutely nothing.

"""

def clear():
    pass

def off():
    pass

def show():
    pass

def set_pixel(x, y, r, g, b):
    print('UnicornHAT HD: setting pixel ({}, {}, {}, {}, {})'.format(x, y, r, g, b))

def brightness(b):
    print('UnicornHAT HD: setting brightness to: {}'.format(b))
    pass

def rotation(r):
    print('UnicornHAT HD: setting rotation to: {}'.format(r))
    pass

def get_shape():
    return 16, 16
