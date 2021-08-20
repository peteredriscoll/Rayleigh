#
#  Copyright (C) 2021 by the authors of the RAYLEIGH code.
#
#  This file is part of RAYLEIGH.
#
#  RAYLEIGH is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 3, or (at your option)
#  any later version.
#
#  RAYLEIGH is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with RAYLEIGH; see the file LICENSE.  If not see
#  <http://www.gnu.org/licenses/>.
#

import sys
import os
import mmap
import collections.abc
import abc

import numpy as np
import matplotlib.pyplot as plt

import lut

if sys.maxsize < 2**63 - 1:
    # We don't want mmap on 32-bit systems where virtual memory is limited.
    use_mmap = False
else:
    use_mmap = True

try:
    import tqdm
    try:
        in_notebook = get_ipython().__class__.__name__ == 'ZMQInteractiveShell'
    except NameError:
        in_notebook = False

    if in_notebook:
        progress = tqdm.tqdm_notebook
    else:
        progress = tqdm.tqdm
except ImportError:
    progress = lambda x: x

def get_bounds(a, start, end):
    a = 0.5 * (a[:-1] + a[1:])
    return np.concatenate([[start], a, [end]], axis=0)

class BaseFile(object):
    @staticmethod
    def get_endian(fd, sig: int, sigtype) -> str:
        """returns > if the file is big endian and < if the file is little endian"""
        dtype = np.dtype(sigtype)
        buf = fd.read(dtype.itemsize)
        if np.frombuffer(buf, dtype="<"+sigtype, count=1)[0] == sig:
            return "<"
        elif np.frombuffer(buf, dtype=">"+sigtype, count=1)[0] == sig:
            return ">"
        else:
            raise IOError("could not determine endianness")

    def __init__(self, filename: str, endian=None, memmap=use_mmap):

        self.fh = open(filename, "rb")
        self._memmap = memmap

        if self._memmap:
            buf = mmap.mmap(self.fh.fileno(), 0, mmap.MAP_SHARED, mmap.ACCESS_READ)
            self.fh.close()
            self.fh = buf

        if endian is None:
            self.endian = self.get_endian(self.fh, 314, 'i4')
        else:
            self.endian = endian

    def get_value(self, dtype: str, shape=[1]):
        dtype = np.dtype(dtype).newbyteorder(self.endian)
        size = np.product(shape)
        if self._memmap:
            out = np.ndarray(shape, dtype=dtype, buffer=self.fh,
                             offset=self.fh.tell(), order='F')
            self.fh.seek(dtype.itemsize * size, os.SEEK_CUR)
        else:
            out = np.fromfile(self.fh, dtype=dtype, count=size)
            out.shape = shape
        if size == 1:
            return out[0]
        else:
            return out

class TimeSeries(object):
    pass

class Spherical_3D_grid(BaseFile):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)

        self.nr, self.ntheta, self.nphi = self.get_value('i4', shape=[3])
        assert(self.nphi == 2 * self.ntheta)

        self.radius = self.get_value('f8', shape=[self.nr])
        self.thetas = self.get_value('f8', shape=[self.ntheta])

class Spherical_3D_value(np.ndarray):
    def __new__(cls, filename, nr, ntheta, nphi, endian, **kwargs):
        f = BaseFile(filename, endian=endian, **kwargs)

        return f.get_value('f8', shape=[nphi, ntheta, nr]).view(type=cls)

class Spherical_3D_TimeSeries(TimeSeries):
    def __init__(self, directory, qcode, snaps):
        super().__init__()

        self.directory = directory
        self.qcode = qcode
        self.snaps = snaps

    def __getitem__(self, ind):
        def getone(i):
            f = os.path.join(self.directory, "{:08d}_grid".format(i))
            grid = Spherical_3D_grid(f)
            f = os.path.join(self.directory, "{:08d}_{:04d}".format(i, self.qcode))
            return Spherical_3D_value(f, grid.nr, grid.ntheta, grid.nphi,
                                      endian=grid.endian)
        if np.isscalar(ind):
            return getone(self.snaps[ind])
        else:
            return [getone(i) for i in self.snaps[ind]]

class Spherical_3D_Snapshot(object):
    def __init__(self, directory, snap):
        self.directory = directory
        self.snap = snap

        f = os.path.join(self.directory, "{:08d}_grid".format(snap))
        grid = Spherical_3D_grid(f)

        self.radius = grid.radius
        self.thetas = grid.thetas
        self.phi_edge = np.linspace(0., 2 * np.pi, grid.nphi)
        self.phis = 0.5 * (self.phi_edge[1:] + self.phi_edge[:-1])

        self.endian = grid.endian

    def q(self, q):
        f = os.path.join(self.directory, "{:08d}_{:04d}".format(self.snap, q))
        return Spherical_3D_value(f, len(self.radius), len(self.thetas),
                                  len(self.phis), endian=self.endian)

    def __getattr__(self, q):
        qcode = lut.parse_quantity(q)[0]
        if qcode is None:
            raise AttributeError("unknown quantity ({})".format(q))
        return self.q(qcode)


class Spherical_3D(object):
    def __init__(self, directory='Spherical_3D'):
        super().__init__()

        self.directory = directory
        files = os.listdir(directory)
        self.snaps = set()
        self.quants = set()

        for f in progress(files):
            snap, quant = f.split('_')
            if quant == 'grid':
                continue
            s = int(snap)
            q = int(quant)
            assert("{:08d}".format(s) == snap)
            assert("{:04d}".format(q) == quant)
            self.snaps.add(s)
            self.quants.add(q)

        self.snaps = list(self.snaps)
        self.snaps.sort()
        self.quants = list(self.quants)
        self.quants.sort()

    def q(self, q):
        return Spherical_3D_TimeSeries(self.directory, q, self.snaps)

    def __getitem__(self, ind):
        return Spherical_3D_Snapshot(self.directory, ind)

    def __getattr__(self, q):
        qcode = lut.parse_quantity(q)[0]
        if qcode is None:
            raise AttributeError("unknown quantity ({})".format(q))
        return self.q(qcode)

class Shell_Slice_file(BaseFile):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)

        self.version = self.get_value('i4')
        self.nrec = self.get_value('i4')

        self.ntheta = self.get_value('i4')
        self.nphi = 2 * self.ntheta
        self.nr = self.get_value('i4')
        self.nq = self.get_value('i4')

        self.qv = self.get_value('i4', shape=[self.nq])

        self.radius = self.get_value('f8', shape=[self.nr])
        self.inds = self.get_value('i4', shape=[self.nr]) - 1
        self.costheta = self.get_value('f8', shape=[self.ntheta])
        self.sintheta = np.sqrt(1.0 - self.costheta**2)

        self.val = self.get_value('f8', shape=[self.nphi, self.ntheta, self.nr, self.nq, self.nrec])

class G_Avgs_file(BaseFile):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)

        self.version = self.get_value('i4')
        self.nrec = self.get_value('i4')
        self.nq = self.get_value('i4')

        self.qv = self.get_value('i4', shape=[self.nq])

        self.vals = np.empty((self.nrec, self.nq), dtype='f8')
        self.time = np.empty((self.nrec,), dtype='f8')
        self.iters = np.empty((self.nrec,), dtype='i4')

        buf = self.get_value(np.dtype([('val', 'f8', (self.nq,)),
                                       ('time', 'f8'),
                                       ('iters', 'i4')]),
                              shape=[self.nrec])

        self.vals = buf['val']
        self.time = buf['time']
        self.iters = buf['iters']

class Shell_Avgs_file(BaseFile):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)

        self.version = self.get_value('i4')
        self.nrec = self.get_value('i4')

        self.nr = self.get_value('i4')
        self.nq = self.get_value('i4')

        if self.version >= 6:
            npcol = self.get_value('i4')

        self.qv = self.get_value('i4', shape=[self.nq])

        self.radius = self.get_value('f8', shape=[self.nr])

        if self.version == 1:
            dtype = np.dtype([('val', 'f8', (self.nr, self.nq)),
                              ('time', 'f8'),
                              ('iters', 'i4')])
        elif self.version < 6:
            dtype = np.dtype([('val', 'f8', (self.nr, 4, self.nq)),
                              ('time', 'f8'),
                              ('iters', 'i4')])
        else:
            # fixme
            dtype = np.dtype([('val', 'f8', (self.nr, 4, self.nq)),
                              ('time', 'f8'),
                              ('iters', 'i4')])

        buf = self.get_value(dtype, shape=[self.nrec])
        self.val = buf['val']
        self.time = buf['time']
        self.iters = buf['iters']


class Rayleigh_TimeSeries(collections.abc.Sequence):
    def __init__(self, base, qcode):
        self.base = base
        self.qcode = qcode

    def __getitem__(self, i):
        return self.base.get_q(i, self.qcode)

    def __len__(self):
        return len(self.base)


class Rayleigh_TimeStep(object):
    def __init__(self, base, i):
        self.base = base
        self.i = i

    def __getattr__(self, name):
        qcode = lut.parse_quantity(name)[0]
        if qcode is None:
            raise AttributeError("unknown quantity '{}'".format(name))
        return self.base.get_q(self.i, qcode)


class Rayleigh_Output(collections.abc.Sequence):
    @abc.abstractmethod
    def get_q(self, i, qcode):
        pass

    def __len__(self):
        return len(self.val)

    def __getattr__(self, name):
        qcode = lut.parse_quantity(name)[0]
        if qcode is None:
            raise AttributeError("unknown quantity '{}'".format(name))
        return Rayleigh_TimeSeries(self, qcode)

    def __getitem__(self, i):
        return Rayleigh_TimeStep(self, i)

    def __init__(self, filecls, directory, subrange=None):
        super().__init__()

        self.directory = directory
        files = os.listdir(directory)
        files.sort()

        if subrange is not None:
            if isinstance(subrange, int):
                subrange = range(0, len(files), subrange)
            files = files[subrange]

        self.val = []
        self.time = []
        self.iter = []
        self.gridpointer = []

        for a in self.attrs:
            setattr(self, a, [])

        for i, f in enumerate(progress(files)):
            m = filecls(os.path.join(directory, f))
            self.val += m.val
            self.time += m.time
            self.iter += m.iter

            self.gridpointer += len(m.val) * [i]

            for a in self.attrs:
                getattr(self, a).append(getattr(m, a))

class Plot2D(abc.ABC):
    @abc.abstractmethod
    def get_coords(self, i):
        pass

    def pcolor(self, i, q, Clear=True, iphi=0, Colorbar=True, **kwargs):
        qcode = lut.parse_quantity(q)[0]
        if qcode is None:
            raise AttributeError("unknown quantity ({})".format(q))
        fig = plt.gcf()
        if Clear:
            fig.clear()

        ax = fig.add_subplot(111)

        X, Y = self.get_coords(i)
        im = ax.pcolormesh(X, Y, self.get_q(i, qcode)[iphi, :, :], **kwargs)
        if Colorbar:
            plt.colorbar(im, ax=ax)
        ax.set_title(f"{lut.latex_formula(q)} at $t={self.time[i]}$")
        ax.set_aspect('equal')


class Meridional_Slices_file(BaseFile):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)

        self.version = self.get_value('i4')
        self.nrec = self.get_value('i4')

        self.nr = self.get_value('i4')
        self.ntheta = self.get_value('i4')
        self.nphi = self.get_value('i4')
        self.nq = self.get_value('i4')

        self.qv = self.get_value('i4', shape=[self.nq])
        self.qvmap = {v: i for i, v in enumerate(self.qv)}

        self.radius = self.get_value('f8', shape=[self.nr])
        self.costheta = self.get_value('f8', shape=[self.ntheta])
        self.sintheta = np.sqrt(1.0 - self.costheta**2)
        self.phi_inds = self.get_value('i4', shape=[self.nphi]) - 1
        if self.nphi == 1:
            self.phi_inds = np.array([self.phi_inds])
        self.phi = np.zeros(self.nphi,dtype='float64')

        dphi = (2*np.pi)/(self.ntheta*2)
        for i in range(self.nphi):
            self.phi[i] = self.phi_inds[i]*dphi

        self.val = []
        self.time = []
        self.iter = []
        for i in range(self.nrec):
            self.val.append(self.get_value('f8', shape=[self.nphi, self.ntheta, self.nr, self.nq]))
            self.time.append(self.get_value('f8'))
            self.iter.append(self.get_value('i4'))


class Meridional_Slices(Rayleigh_Output, Plot2D):
    attrs = ("radius", "costheta", "sintheta", "qvmap")

    def __init__(self, directory='Meridional_Slices'):
        super().__init__(Meridional_Slices_file, directory)

        self.theta = [np.arccos(x) for x in self.costheta]
        self.costheta_bounds = [np.cos(get_bounds(t, np.pi, 0.)) for t in self.theta]
        self.sintheta_bounds = [np.sqrt(1.0 - ct**2) for ct in self.costheta_bounds]
        self.radius_bounds = [get_bounds(r, r[0] + 0.5 * (r[0] - r[1]),
                                         r[-1] - 0.5 * (r[-2] - r[-1]))
                              for r in self.radius]

    def get_coords(self, i):
        igrid = self.gridpointer[i]
        X = self.sintheta_bounds[igrid][:, None] * self.radius_bounds[igrid][None, :]
        Y = self.costheta_bounds[igrid][:, None] * self.radius_bounds[igrid][None, :]
        return X, Y

    def get_q(self, i, qcode):
        igrid = self.gridpointer[i]
        return self.val[i][:, :, :, self.qvmap[igrid][qcode]]


class Equatorial_Slices_file(BaseFile):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)

        self.version = self.get_value('i4')
        self.nrec = self.get_value('i4')

        self.nphi = self.get_value('i4')
        self.nr = self.get_value('i4')
        self.nq = self.get_value('i4')

        self.qv = self.get_value('i4', shape=[self.nq])
        self.qvmap = {v: i for i, v in enumerate(self.qv)}

        self.radius = self.get_value('f8', shape=[self.nr])

        dphi = 2 * np.pi / self.nphi
        self.phi = np.arange(self.nphi) * dphi

        self.val = []
        self.time = []
        self.iter = []
        for i in range(self.nrec):
            self.val.append(self.get_value('f8', shape=[self.nphi, self.nr, self.nq]))
            self.time.append(self.get_value('f8'))
            self.iter.append(self.get_value('i4'))


class Equatorial_Slices(Rayleigh_Output, Plot2D):
    attrs = ("radius", "phi", "qvmap")

    def __init__(self, directory='Equatorial_Slices'):
        super().__init__(Equatorial_Slices_file, directory)

        self.phi_bounds = [get_bounds(p, 0., 2. * np.pi) for p in self.phi]
        self.radius_bounds = [get_bounds(r, r[0] + 0.5 * (r[0] - r[1]),
                                         r[-1] - 0.5 * (r[-2] - r[-1]))
                              for r in self.radius]

    def get_coords(self, i):
        igrid = self.gridpointer[i]
        X = np.cos(self.phi_bounds[igrid][:, None]) * self.radius_bounds[igrid][None, :]
        Y = np.sin(self.phi_bounds[igrid][:, None]) * self.radius_bounds[igrid][None, :]
        return X, Y

    def get_q(self, i, qcode):
        igrid = self.gridpointer[i]
        return self.val[i][None, :, :, self.qvmap[igrid][qcode]]


class PDE_Coefficients(BaseFile):
    nconst = 10
    nfunc = 14

    def __init__(self, filename='equation_coefficients', **kwargs):
        super().__init__(filename, **kwargs)

        self.version = self.get_value('i4')
        self.cset = self.get_value('i4', shape=[self.nconst])
        self.fset = self.get_value('i4', shape=[self.nfunc])

        self.constants = self.get_value('f8', shape=[self.nconst])
        self.nr = self.get_value('i4')
        self.radius = self.get_value('f8', shape=[self.nr])
        self.functions = self.get_value('f8', shape=[self.nr, self.nfunc])

        # aliases
        self.density = self.rho = self.functions[:,1-1]
        self.dlnrho  = self.functions[:,8-1]
        self.d2lnrho = self.functions[:,9-1]

        self.temperature = self.T = self.functions[:,4-1]
        self.dlnT        = self.functions[:,10-1]

        self.dsdr = self.functions[:,14-1]

        self.heating = self.functions[:,6-1]*self.constants[10-1]/self.rho/self.T

        self.nu   = self.functions[:,3-1]
        self.dlnu = self.functions[:,11-1]
        self.kappa    = self.functions[:,5-1]
        self.dlnkappa = self.functions[:,12-1]
        self.eta    = self.functions[:,7-1]
        self.dlneta = self.functions[:,13-1]
