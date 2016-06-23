#!/usr/bin/env python
#-*- coding:utf-8 -*- # # Software License Agreement (GPLv2 License) #
# Copyright (c) 2012 TheCorpora SL #
# This program is free software; you can redistribute it and/or 
# modify it under the terms of the GNU General Public License as 
# published by the Free Software Foundation; either version 2 of
# the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of 
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. 
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License 
# along with this program; if not, write to the Free Software 
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, 
# MA 02110-1301, USA.
#
# Authors: Miguel Angel Julian <miguel.julian@openqbo.com>;
#          Daniel Cuadrado <daniel.cuadrado@openqbo.com>;
#          Arturo Bajuelos <arturo@openqbo.com>; 
#          Sergio Merino <s.merino@openqbo.com>;

import urllib
import cherrypy
import json
import os
import subprocess
import hashlib
import roslib
#import rospy
import time
import cgi
import shutil
import tempfile
import commands
import rospy
#import sys
from time import sleep
import threading
import alsaaudio
from types import ListType
from multiprocessing import Process
from mako.template import Template
from tabsClass import TabClass
from xmmsclient import sync, XMMSError, PLAYBACK_STATUS_PAUSE, \
                       PLAYBACK_STATUS_PLAY, PLAYBACK_STATUS_STOP


roslib.load_manifest('music')
from music.srv import Action2Result
from music.msg import Action
roslib.load_manifest('qbo_webi')
roslib.load_manifest('jd_logging')
from jd_logging import jd
#roslib.load_manifest('jd_data_collect')
#from jd_data_collect import collect_user_action as cua
roslib.load_manifest('jd_db')
from jd_db import jd_db
roslib.load_manifest('jd_tools')
from jdutil import say
from jd_store import release_space

json_str = lambda info : json.dumps(info, ensure_ascii=False)
fstr = lambda x :"'%s'" % x

_lock_ = threading.Lock()
_lock_acquire = _lock_.acquire
_lock_release = _lock_.release


info_values = ('id', 'title', 'artist', 'album', 'url')


''' 
    aux classes to help in the upload proccess
    source: http://tools.cherrypy.org/wiki/DirectToDiskFileUpload 
'''
class myFieldStorage(cgi.FieldStorage):
    """Our version uses a named temporary file instead of the default
    non-named file; keeping it visibile (named), allows us to create a
    2nd link after the upload is done, thus avoiding the overhead of
    making a copy to the destination filename."""
    
    def make_file(self, binary=None):
        return tempfile.NamedTemporaryFile()


def noBodyProcess():
    """Sets cherrypy.request.process_request_body = False, giving
    us direct control of the file upload destination. By default
    cherrypy loads it to memory, we are directing it to disk."""
    cherrypy.request.process_request_body = False


cherrypy.tools.noBodyProcess = cherrypy.Tool('before_request_body', \
                                             noBodyProcess)
# remove any limit on the request body size; cherrypy's default is 100MB
# (maybe we should just increase it ?)
cherrypy.server.max_request_body_size = 0

# increase server socket timeout to 60s; we are more tolerant of bad
# quality client-server connections (cherrypy's defult is 10s)
cherrypy.server.socket_timeout = 60
#jd setting 
cherrypy.server.thread_pool = 50


''' from here the normal stuff '''
class XMMS2Manager(TabClass):
    
    def __init__(self, language):
        self.language = language
        self.webi_path = roslib.packages.get_pkg_dir("qbo_webi") + "/src/"

        htmlfile = self.webi_path + 'xmms2/templates/xmms2Template.html' 
        self.htmlTemplate = Template(filename=htmlfile)

        jsfile = self.webi_path + 'xmms2/templates/xmms2Template.js' 
        self.jsTemplate = Template(filename=jsfile)
        rospy.wait_for_service("/music/service")
        self.music_service = rospy.ServiceProxy("/music/service", Action2Result)
        self.music_topic = rospy.Publisher("/music/topic", Action).publish
    def action(self, action, params=None, type="service"):
        jd.debug("action=%s, params=%s, type=%s" % (action, params, type))
        if params is None:
            params = "{}"
        else:
            params = json.dumps(params)
        if type == "service":
            while 1:
                try:
                    # 这里可能有问题, 有可能积累大量的wait_for_service
                    rospy.wait_for_service("/music/service")
                    result = self.music_service(action, params).result
                    break
                except rospy.service.ServiceException, e:
                    # 当频繁访问节点时，节点无法处理异常。
                    jd.warn("ros 音乐节点繁忙" +str(e))  
                    sleep(0.05)
                    continue
            return json.loads(result)
        elif type == "topic":
            msg = Action()
            msg.action = action
            msg.params = params
            self.music_topic(msg)
            return {"status":0, "data":"%s, %s" % (action, params)}
        else:
            return {"status":-1, "data":"type must be service or topic"}
            
            

    def get_playlist(self):
        ret = self.action("get_playlist")
        return ret["data"]  

    @cherrypy.expose
    def moveSong(self, oldpos, newpos):
        """鼠标拖拽音乐拉条 
        """
        jd.debug("entry")
        ret = self.action("exchange", {"old":oldpos, "new":newpos})
        return str(ret["data"])

    def play(self):
        """播放歌曲
        """
        self.action("play")
        return self.getSelectedSong()
   

    @cherrypy.expose
    def get_current_status(self):
        jd.debug("entry")
        #获得当前播放状态
        ret = self.action("get_current_status")
        jd.debug(ret["data"])
        #print "%(id)s,%(volume)s,%(status)s" % ret["data"]
        return "%(id)s,%(volume)s,%(status)s" % ret["data"]
   
    def get_current_id(self):
        ret = self.action("get_current_id")
        return ret["data"]

    def get_status(self):
        ret = self.action("status")
        return ret["data"]

        
    @cherrypy.expose
    def getSelectedSong(self, flag = 0):
        """如果flag = 0 返回当前歌曲id
           如果flag = 1 返回当前id，当前歌曲声音，当前歌曲状态
        """
        if not flag:
            return str(self.get_current_id())
        else:
            #获得当前播放状态
            return self.get_current_status()

    def pause(self):
        """暂停
        """
        self.action("pause")
        return self.getSelectedSong()

    def get_volume(self):
        ret = self.action("get_volume")  
        return ret["data"]

    def set_volume(self, volume): 
        self.action("set_volume", {"volume":volume}, type="topic")

    @cherrypy.expose
    def setVolume(self, volume):
        """设置播放音乐音量
        """
        jd.debug("entry")
        self.set_volume(int(volume))
        return str(self.get_volume())
    
            
    @cherrypy.expose
    def playSong(self, ident):
        """播放歌曲 双击歌曲条播放音乐
        """
        self.action("play",{"ident":int(ident)})


    @cherrypy.expose
    def getVolume(self):
        """获得当前播放器的声音
        """
        jd.debug("entry")
        return str(self.get_volume())
            
    @cherrypy.expose
    def playpause(self):
        jd.debug("entry")
        status = self.get_status() #返回当前播放器状态（int）。0:停止，1：播放，2暂停
        jd.debug("当前的播放状态是'%s'" % (("停止", "播放", "暂停")[status]))
        jd.info("播放/暂停歌曲。")
        #播放
        if status == PLAYBACK_STATUS_PLAY:
            self.pause()
        #停止/暂停 
        else:
            self.play()
        return str(self.get_status())

    @cherrypy.expose
    def getStatus(self):
        """获得当前音乐播放器状态\n返回字符串(str),'0':停止，'1'：播放，'2':暂停
        """
        jd.debug("entry")
        return str(self.get_status())


    @cherrypy.expose
    def stop(self):
        """停止音乐
        """
        jd.debug("entry")
        self.action("stop", type="topic")

    @cherrypy.expose
    def next(self):
        """播放下一首音乐，当前为最后一首歌曲时播放第一首歌曲
        """
        jd.debug("entry")
        self.action("next", type="topic")
        return self.getSelectedSong()

    @cherrypy.expose
    def previous(self):
        """播放上一首音乐，当前为第一首时播放当前歌曲
        """
        jd.debug("entry")
        self.action("previous", type="topic")
        return self.getSelectedSong()

    def music_clean(self):
        jd.info("音乐数据初始化")
        self.action("clear", type="topic")
    
    @cherrypy.expose
    def getActivePlaylistSongs(self):
        """将显示文件名改为歌曲文件名，并传给页面
        """
        jd.debug("entry")
        songslist = self.get_playlist()
        jd.debug(songslist)
        for song in songslist:
            
            song['title'] = song["name"]
           
        return json.dumps(songslist)

    @cherrypy.expose
    def delete(self, ident):
        """删除歌曲函数
        """
        jd.debug("entry")
        self.action("delete",{"ident":int(ident)})
        return  self.getActivePlaylistSongs()

    @cherrypy.expose
    def xmms2Js(self, **params):
        """加入js模板
        """
        jd.debug("entry")
        return self.jsTemplate.render(language=self.language)

    
    @cherrypy.expose
    def index(self):
        """加入html模板
        """
        jd.debug("entry")
        return self.htmlTemplate.render(language=self.language)
        #self.insert_music_data(file_md5, str(filename))

    def addMusic(self, songObject):

        """
            功能描诉：添加歌曲，当可以添加时在song目录中添加歌曲
            返回:当歌曲无法添加时返回错误歌曲文件名
        """
        filename = songObject.filename
        #非0字节文件 
        if hasattr(songObject.file, "name"):
            src_path = songObject.file.name
            md5 = hashlib.md5(open(src_path).read()).hexdigest()
            dest_path = os.path.join(os.path.dirname(src_path), md5)
            if not os.path.isfile(dest_path):
                os.link(src_path, dest_path)
               
            return self._addMusic(dest_path, filename)
        #处理0字节的文件.如果为0字节返回错误文件名
        else:
            jd.warn("音乐文件%s大小为0" % songObject.filename)
            return songObject.filename

    def add_local(self, file_path, name):
        ret = self.action("add_local", {"file_path":file_path, "name":name})
        return ret
          
    @cherrypy.expose
    @cherrypy.tools.noBodyProcess()
    def upload(self, theFile=None):
        """upload action
        We use our variation of cgi.FieldStorage to parse the MIME
        encoded HTML form data containing the file."""
        jd.debug("entry")
        jd.info("上传文件")
        
        # the file transfer can take a long time; by default cherrypy
        # limits responses to 300s; we increase it to 1h
        cherrypy.response.timeout = 3600
        
        # convert the header keys to lower case
        lcHDRS = {}
        for key, val in cherrypy.request.headers.iteritems():
            lcHDRS[key.lower()] = val
        
        # at this point we could limit the upload on content-length...
        # incomingBytes = int(lcHDRS['content-length'])
        
        # create our version of cgi.FieldStorage to parse the MIME encoded
        # form data where the file is contained
        formFields = myFieldStorage(fp=cherrypy.request.rfile,
                                    headers=lcHDRS,
                                    environ={'REQUEST_METHOD':'POST'},
                                    keep_blank_values=True)
        
        # we now create a 2nd link to the file, using the submitted
        # filename; if we renamed, there would be a failure because
        # the NamedTemporaryFile, used by our version of cgi.FieldStorage,
        # explicitly deletes the original filename
        theFile = formFields['theFile']

        #批量上传文件
        if type(theFile) is ListType:
            for songFile in theFile:
                if songFile.filename:
                #将歌曲注册到系统中，如果歌曲不可播放返回不可播放歌曲文件名
                    self.add_local(songFile.file.name, songFile.filename)
        else:
            if theFile.filename:
                self.add_local(theFile.file.name, theFile.filename)
        
        return '<form id="Form" action="/" method="post">' \
                 '<input type="hidden" name="activeTab" value="5" />' \
                '</form>' \
                '<script>document.getElementById("Form").submit();</script>'

    @cherrypy.expose
    def musicApi(self): 
        """提供应用程序接口api
           http://0.0.0.0:7070/xmms2/musicApi
           返回json信息
        """
        jd.debug("entry")
        songslist = self.get_playlist()
        try:
            for song in songslist:
                song[u'filename'] = song["name"]
        except:
            message = "应用程序接口不可用"
            return message 
        return json.dumps(songslist, ensure_ascii=False)

    @cherrypy.expose
    def search(self, value= None): 
        """提供查询接口
           http://0.0.0.0:7070/xmms2/search?value=小苹果
           返回过滤后的json信息
        """
        jd.debug("entry")
        searchlist = []
        songslist = self.get_playlist()
        for song in songslist:
            filename = self.urlToSongName(song['url'])
            song[u'filename'] = filename
            if not value or value in ".".join(filename.split(".")[:-1]):
                searchlist.append(song)
        return json.dumps(searchlist, ensure_ascii=False)

    @cherrypy.expose
    def playnet(self, music_info=None): 
        """
           post json
           http://ip:port/xmms2/playnet
           提供在线播放音乐接口
        """
        
        message = {"status":"0", "message":u"成功获得状态"} ,
        jd.debug("entry")
        res = self.action("play_net", {"music_infos":json.loads(music_info)}) 
        if res["status"] != 0:
            message = {"status":"-1", "message":u"播放链接异常"},
        
        return json_str(message)
   
    @cherrypy.expose
    def netplay(self):
        """app播放歌曲接口
           /xmms2/netplay
        """
        jd.debug("entry")
        self.play()
        return self.getStatus()

    @cherrypy.expose
    def netpause(self):
        """app暂停音乐接口
           /xmms2/netpause
        """
        jd.debug("entry")
        self.pause()
        return  self.getStatus()

    def _get_mode(self):
        ret = self.action("get_mode")
        return ret["data"]

    @cherrypy.expose
    def get_mode(self):
        return str(self._get_mode())

    @cherrypy.expose
    def swith_mode(self):
         
        current = int(self._get_mode())
        mode = (current + 1) % 3 
        self.action("set_mode", {"mode":mode})
        return self.get_mode()
