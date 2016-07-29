"""


mweigert@mpi-cbg.de

"""

import numpy as np
from biobeam import Bpm3d
from gputools import convolve_spatial3
from collections import namedtuple

def _perm_inverse(perm):
    inverse = [0] * len(perm)
    for i, p in enumerate(perm):
        inverse[p] = i
    return inverse




class SimLSM_Base(object):
    _GridSaveObject = namedtuple("GridSave",["grid_dim","u0"])

    perm_illum = (2,0,1)
    perm_illum_inv= _perm_inverse(perm_illum)

    def __init__(self, dn = None,
                 signal = None,
                 shape = None,
                 size = None,
                 units = None,
                 lam_illum = .5,
                 NA_illum = .1,
                 lam_detect = .5,
                 NA_detect = .7,
                 n0 = 1.33,
                 n_volumes = 1,
                 zfoc_illum = None,
                 simul_xy_illum = None,
                 simul_z_illum = 1,
                 simul_xy_detect = None,
                 simul_z_detect = 1):



        self.dn = dn
        self.signal  = signal

        self._bpm_illum = Bpm3d(
                        size = self._trans_illum(size, shape_style="xyz"),
                        shape = self._trans_illum(shape, shape_style="xyz"),
                        dn = self._trans_illum(dn, copy = True),
                        units = self._trans_illum(units, shape_style="xyz"),
                        lam = lam_illum,
                        simul_xy=simul_xy_illum,
                        simul_z=simul_z_illum,
                        n_volumes=n_volumes,
                          n0 = n0)

        self._bpm_detect = Bpm3d(size = size,
                          shape = shape,
                          dn = dn,
                          units = units,
                          lam = lam_detect,
                          simul_xy=simul_xy_detect,
                          simul_z=simul_z_detect,
                          n_volumes=n_volumes,
                          n0 = n0)


        self.NA_illum =  NA_illum
        self.NA_detect =  NA_detect
        self.zfoc_illum = zfoc_illum
        self.dn = self._bpm_detect.dn
        self.units = self._bpm_detect.units
        self.Nx, self.Ny, self.Nz = self._bpm_detect.shape
        self.size = self._bpm_detect.size
        self._last_grid_u0 = self._GridSaveObject(None,None)
        self._prepare_u0_all()

    def _trans_illum(self,obj, inv = False, copy=False, shape_style="zyx"):
        """handles the transformation between illumination and detection coords and shapes

        _trans_illum(dn).shape is volume shape in illumination space

        """
        perm = self.perm_illum_inv if inv else self.perm_illum
        if obj is None:
            return None
        if isinstance(obj,np.ndarray):
            if copy:
                return obj.transpose(perm).copy()
            else:
                return obj.transpose(perm)
        if isinstance(obj,(list, tuple)):
            if shape_style=="zyx":
                return type(obj)([obj[p] for p in perm])
            elif shape_style=="xyz":
                return type(obj)([obj[::-1][p] for p in perm])[::-1]
            else:
                raise NotImplementedError()
        else:
            raise NotImplementedError()


    def _prepare_u0_illum(self, zfoc = None ):
        raise NotImplementedError()

    def _prepare_u0_all(self):
        self.u0_detect = self._bpm_detect.u0_beam(NA = self.NA_detect, zfoc = 0.)
        self._prepare_u0_illum(self.zfoc_illum)


    def propagate_illum(self,cz = 0, **bpm_kwargs):
        """cz in microns from center axis"""

        # the illumination pattern is shifted
        bpm_kwargs.update({"return_comp":"intens"})
        offset = int(cz/self._bpm_illum.dy)

        assert abs(offset)<= self.u0_illum.shape[0]/2

        print "offset: ",offset
        u0 = np.roll(self.u0_illum, offset ,axis=0)
        u = self._bpm_illum.propagate(u0,**bpm_kwargs)
        return self._trans_illum(u, inv = True)

    def psf(self, c = [0,0,0], zslice = 16, **bpm_kwargs):
        """
        c = [0,10,-10] relative to center in microns
        c = [cz,cy,cx]
        """
        u0 = np.roll(np.roll(self.u0_detect,int(c[1]/self._bpm_detect.dy),axis=0),
                     int(c[2]/self._bpm_detect.dx),axis=1)


        offset_z = int(c[0]/self._bpm_detect.units[-1])
        u1 = self._bpm_detect.propagate(u0 = u0, offset=self.Nz/2+offset_z,
                                        return_shape="last",**bpm_kwargs)

        #refocus
        u2 = self._bpm_detect.propagate(u0 = u1.conjugate(),
                                       free_prop=True,
                                       #offset=Nz/2+c[0],
                                       return_shape="full",return_comp="intens",
                                        **bpm_kwargs)

        if zslice is None:
            return u2
        else:
            u2 = np.roll(u2,offset_z,axis=0)[self.Nz/2-zslice:self.Nz/2+zslice]
            return u2



    def psf_grid_z(self, cz = 0, grid_dim = (4,4), zslice = 16,**bpm_kwargs):
        """cz in microns relative to the center
        """

        print "computing psf grid %s..."%(str(grid_dim))


        offset_z = int(cz/self._bpm_detect.units[-1])



        n_y, n_x = grid_dim
        Nb_x, Nb_y = self._bpm_detect.simul_xy[0]/n_x, self._bpm_detect.simul_xy[1]/n_y

        # get the offset positions
        xs = [-self._bpm_detect.simul_xy[0]/2+n*Nb_x+Nb_x/2 for n in xrange(n_x)]
        ys = [-self._bpm_detect.simul_xy[1]/2+n*Nb_y+Nb_y/2 for n in xrange(n_y)]

        # this is expensive, so memoize it if we use it several times after
        if self._last_grid_u0.grid_dim == grid_dim:
            print "using saved grid"
            u0 = self._last_grid_u0.u0
        else:
            u0 = np.sum([np.roll(np.sum([np.roll(self.u0_detect,_y,axis=0) for _y in ys],axis=0),_x, axis=1) for _x in xs],axis=0)
            self._last_grid_u0 = self._GridSaveObject(grid_dim,u0)

        u0 = self._bpm_detect.propagate(u0 = u0, offset=self.Nz/2+offset_z,
                                        return_shape="last",
                                        return_comp="field",
                                        **bpm_kwargs)

        #refocus
        u = self._bpm_detect.propagate(u0 = u0.conjugate(),
                                       free_prop=True,
                                       #offset=Nz/2+c[0],
                                       return_shape="full",
                                       return_comp="intens",
                                       **bpm_kwargs)

        if zslice is None:
            return u
        else:
            u = np.roll(u,offset_z,axis=0)[self.Nz/2-zslice:self.Nz/2+zslice]
            return u

        # if with_sheet:
        #     sheet = self.propagate(z)
        #     h *= abs(sheet)**2
        # return h

    def simulate_image_z(self, cz = 0,
                         psf_grid_dim = (8,8),
                         zslice = 16,
                         conv_sub_blocks = (1,1),
                         conv_pad_factor = 2,
                         conv_mode = "wrap",
                         **bpm_kwargs):
        if self.signal is None:
            raise ValueError("no signal defined (signal)!")

        # illumination

        print "illuminating at z= %s mu"%cz


        u = self.propagate_illum(cz = cz,**bpm_kwargs)
        psfs = self.psf_grid_z(cz = cz, grid_dim=psf_grid_dim, zslice=zslice,**bpm_kwargs)

        offset_z = int(cz/self._bpm_detect.units[-1])

        s = slice(self.Nz/2+offset_z-zslice,self.Nz/2+offset_z+zslice)
        signal = u[s]*self.signal[s]

        # signal = (u*self.signal)[self.Nz/2+offset_z-zslice:self.Nz/2+offset_z+zslice]


        print "convolving: %s %s"%(signal.shape,psfs.shape)
        #convolve
        conv = convolve_spatial3(signal, psfs,
                                 grid_dim = (1,)+psf_grid_dim,
                                 sub_blocks=(1,)+conv_sub_blocks,
                                 pad_factor=conv_pad_factor,
                                 mode = conv_mode)



        return conv


if __name__ == '__main__':
    pass