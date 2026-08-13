#!/bin/bash
# WRF/WPS runtime environment. Strips Anaconda MPI from PATH.
# Source this in compile and forecast scripts only — not in Python post.

ROOT="/home/kenny/phoenix-wrf"
PREFIX="$ROOT/opt/prefix"
export PHX_ROOT="$ROOT"

export PATH="$ROOT/opt/bin:$PREFIX/usr/bin:/usr/local/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$PREFIX/usr/lib/x86_64-linux-gnu:$PREFIX/usr/lib/x86_64-linux-gnu/openmpi/lib:$PREFIX/usr/lib/x86_64-linux-gnu/hdf5/serial:/usr/lib/x86_64-linux-gnu"
# gcc/gfortran search path for -lmpi etc. (Ubuntu OpenMPI wrappers omit this -L)
export LIBRARY_PATH="$PREFIX/usr/lib/x86_64-linux-gnu:$PREFIX/usr/lib/x86_64-linux-gnu/openmpi/lib:${LIBRARY_PATH:-}"

unset CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PYTHON_EXE CONDA_SHLVL PYTHONHOME PYTHONPATH
unset CONDA_PROMPT_MODIFIER CONDA_EXE CONDA_PYTHON_SYSCONFIGDATA_NAME

export CC=gcc
export CXX=g++
export FC=gfortran
export F77=gfortran
export FCFLAGS="-m64"
export FFLAGS="-m64"

export NETCDF="$ROOT/opt/netcdf"
export HDF5="/usr"
export JASPERLIB="$ROOT/opt/grib2/lib"
export JASPERINC="$ROOT/opt/grib2/include"
export WRF_DIR="$ROOT/src/WRF"

export OPAL_PREFIX="$PREFIX/usr"
export OMPI_FC=gfortran
export OMPI_CC=gcc
export OMPI_MCA_btl=vader,self,tcp
export OMPI_MCA_pml=ob1

export DIR="$ROOT/opt"
hash -r
