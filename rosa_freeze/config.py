import ConfigParser
import os
import sys
import fcntl

#####################################################
# USAGE:
#
# from rosa_freeze.config import Config
#
# cfg = Config()
# cfg['aaa']['bbb'] = 'ccc'
# print cfg['aaa']['bbb']
#####################################################

def mkdirs(path):
    ''' the equivalent of mkdir -p path'''
    if os.path.exists(path):
        return
    path = os.path.normpath(path)
    items = path.split('/')
    p = ''
    for item in items:
        p += '/' + item
        if not os.path.isdir(p):
            os.mkdir(p)

class Section(dict):
    def __init__(self, config, conf_path, section):
        self.section = section
        self.config = config
        self.conf_path = conf_path
        if not section in self.config.sections():
            self.config.add_section(self.section)
            self.save()

    def save(self):
        configfile = open(self.conf_path, 'wb')
        fcntl.flock(configfile, fcntl.LOCK_EX)
        self.config.write(configfile)
        fcntl.flock(configfile, fcntl.LOCK_UN)

    def __setitem__(self, key, value):
        '''NOTE: value is ignored'''
        if key in self and self[key] == value:
            return
        super(Section, self).__setitem__(key, value)
        self.config.set(self.section, key, value)
        self.save()

    def __getitem__(self, key):
        if super(Section, self).__contains__(key):
            return super(Section, self).__getitem__(key)
        try:
            res = self.config.get(self.section, key)
        except ConfigParser.NoOptionError, ex:
            print(_('error in config "%(path)s": %(exception)s') % (self.conf_path, str(ex)))
            exit(1)

class Config(dict):
    default_freeze_type = 'folder'
    default_freeze_folder = '/rfreeze'
    default_freeze_device = ''
    default_restore_point_folder = '/restore_points'

    def __init__(self, conf_path='/etc/rfreeze.cfg', main_conf=True):
        self.conf_path = os.path.expanduser(conf_path)
        self.main_conf = main_conf
        init = False
        if not os.path.isfile(self.conf_path):
            mkdirs(os.path.dirname(self.conf_path))
            init = True

        self.config = ConfigParser.RawConfigParser()
        self.config.read(self.conf_path)

        sections = self.config.sections()
        for section in sections:
            opts = self.config.options(section)
            for opt in opts:
                super(Section, self[section]).__setitem__(opt, self.config.get(section, opt))


        if init and main_conf:
            self.first_start()

    def __setitem__(self, key, value):
        '''NOTE: value is ignored'''
        if super(Config, self).__contains__(key):
            return
        super(Config, self).__setitem__(key, Section(self.config, self.conf_path, key))

    def __getitem__(self, key):
        if not super(Config, self).__contains__(key):
            self[key] = []
        return super(Config, self).__getitem__(key)

    def first_start(self):
        self['freeze']['type'] = Config.default_freeze_type
        self['freeze']['device'] = Config.default_freeze_device
        self['freeze']['folder'] = Config.default_freeze_folder
        self['restore_points']['folder'] = Config.default_restore_point_folder
