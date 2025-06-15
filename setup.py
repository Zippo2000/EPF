from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy

# Requirements aus Datei lesen
def load_requirements():
    with open('requirements.txt', 'r') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
    name="cpy_module",
    ext_modules=cythonize([
        Extension(
            "cpy",
            sources=["cpy.pyx"],
            include_dirs=[numpy.get_include()],
            define_macros=[("NPY_NO_DEPRECATED_API", "NPY_1_7_API_VERSION")]
        )
    ], language_level=3),
    install_requires=load_requirements(),
    setup_requires=["numpy", "cython"]
)
