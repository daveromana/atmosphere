"""
ImageManager:
    Remote Openstack Image management (euca2ools 1.3.1)

EXAMPLE USAGE:
from service.drivers.openstackImageManager import ImageManager
manager = ImageManager()
new_image = manager.upload_image("/home/esteve/images/wsgi_v3/sangeeta_esteve_DjangoWSGIStack-v3_11072012_105500.img", 'Django WSGI Stack')
In [4]: new_image
Out[4]: <Image {u'status': u'active', u'name': u'Django WSGI Stack',
    u'deleted': False, u'container_format': u'ovf', u'created_at':
    u'2012-11-20T20:35:01', u'disk_format': u'raw', u'updated_at':
    u'2012-11-20T20:37:01', u'id': u'07b745b1-a8ca-4751-afc0-35f524f332db',
    u'owner': u'4ceae82d4bd44fb48aa7f5fcd36bcc4e', u'protected': False,
    u'min_ram': 0, u'checksum': u'3849fe55340d5a75f077086b73c349e4',
    u'min_disk': 0, u'is_public': True, u'deleted_at': None, u'properties': {},
    u'size': 10067378176}>

"""


from atmosphere.logger import logger

from service.drivers.common import _connect_to_keystone, _connect_to_nova,\
                                   _connect_to_glance, find

class ImageManager():
    """
    Convienence class that uses a combination of boto and euca2ools calls
    to remotely download an image from the cloud
    * See http://www.iplantcollaborative.org/Zku
      For more information on image management
    """
    glance = None
    nova = None
    keystone = None

    @classmethod
    def lc_driver_init(self, lc_driver, *args, **kwargs):
        lc_driver_args = {
            'username': lc_driver.key,
            'password': lc_driver.secret,
            'tenant_name': lc_driver._ex_tenant_name,
            'auth_url': lc_driver._ex_force_auth_url,
            'region_name': lc_driver._ex_force_service_region
        }
        lc_driver_args.update(kwargs)
        manager = ImageManager(*args, **lc_driver_args)
        return manager

    def __init__(self, *args, **kwargs):
        if len(args) == 0 and len(kwargs) == 0:
            raise KeyError("Credentials missing in __init__. ")
        self.newConnection(*args, **kwargs)

    def newConnection(self, *args, **kwargs):
        """
        Can be used to establish a new connection for all clients
        """
        self.keystone = _connect_to_keystone(*args, **kwargs)
        self.glance = _connect_to_glance(self.keystone, *args, **kwargs)
        self.nova = _connect_to_nova(*args, **kwargs)


    def upload_euca_image(self, name, image, kernel=None, ramdisk=None):
        """
        Upload a euca image to glance..
            name - Name of image when uploaded to OpenStack
            image - File containing the image
            kernel - File containing the kernel
            ramdisk - File containing the ramdisk
        Requires 3 separate uploads for the Ramdisk, Kernel, and Image
        """
        opts = {}
        if kernel:
            new_kernel = self.upload_image(kernel,
                                           'eki-%s' % name,
                                           'aki', 'aki', True)
            opts['kernel_id'] = new_kernel.id
        if ramdisk:
            new_ramdisk = self.upload_image(ramdisk,
                                            'eri-%s' % name,
                                            'ari', 'ari', True)
            opts['ramdisk_id'] = new_ramdisk.id
        new_image = self.upload_image(image, name, 'ami', 'ami', True, opts)
        return new_image

    def upload_image(self, download_loc, name,
                     container_format='ovf',
                     disk_format='raw',
                     is_public=True, properties={}):
        """
        Defaults ovf/raw are correct for a eucalyptus .img file
        """
        new_meta = self.glance.images.create(name=name,
                                             container_format=container_format,
                                             disk_format=disk_format,
                                             is_public=is_public,
                                             properties=properties,
                                             data=open(download_loc))
        return new_meta

    def download_image(self, download_dir, image_id):
        raise NotImplemented("not yet..")

    def create_image(self, instance_id, name=None, username=None, **kwargs):
        metadata = kwargs
        if not name:
            name = 'Image of %s' % instance_id
        servers = [server for server in
                self.nova.servers.list(search_opts={'all_tenants':1}) if
                server.id == instance_id]
        if not servers:
            return None
        server = servers[0]
        return self.nova.servers.create_image(server, name, metadata)

    def delete_images(self, image_id=None, name=None):
        if not image_id and not name:
            raise Exception("delete_image expects a name or id as keyword"
            " argument")

        if name:
            images = [img for img in self.list_images()
                      if name in img.name]
        else:
            images = [self.glance.images.get(image_id)]

        if len(images) == 0:
            return False
        for image in images:
            self.glance.images.delete(image)

        return True

    def list_images(self):
        return [img for img in self.glance.images.list()]

    def get_image_by_name(self, name):
        for img in self.glance.images.list():
            if img.name == name:
                return img
        return None

    #Image sharing
    def shared_images_for(self, tenant_name=None, image_name=None):
        """

        @param can_share
        @type Str
        If True, allow that tenant to share image with others
        """
        if tenant_name:
            tenant = find(self.keystone.projects, name=tenant_name)
            return self.glance.image_members.list(member=tenant)
        if image_name:
            image = self.get_image_by_name(image_name)
            return self.glance.image_members.list(image=image)

    def share_image(self, image_name, tenant_name, can_share=False):
        """

        @param can_share
        @type Str
        If True, allow that tenant to share image with others
        """
        image = self.get_image_by_name(image_name)
        tenant = find(self.keystone.projects, name=tenant_name)
        return self.glance.image_members.create(
                    image, tenant.id, can_share=can_share)

    def unshare_image(self, image_name, tenant_name):
        """

        @param can_share
        @type Str
        If True, allow that tenant to share image with others
        """
        image = self.get_image_by_name(image_name)
        tenant = find(self.keystone.projects, name=tenant_name)
        return self.glance.image_members.delete(image.id, tenant.id)
