#!/usr/bin/env python
#
# Copyright (c) 2007-2008 Arluison Guillaume
# http://www.arluison.com
#
# This file is part of WDog.
#
# WDog is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# WDog is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with WDog.  If not, see <http://www.gnu.org/licenses/>.
#

'''
WDog or WatchDog is a daemon coordinator and can even turns simple
programs into a daemon. The basic functionalities looks like /etc/init.d scripts 
and apachectl combined.
It simplifies the execution, stop, notify, log... of these daemons and take care
to restart them if anything has gone wrong.
It can also check the space left on disks.
If anything's wrong it will try to react accordingly by itself and in any case
will try to contact you either by email or on msn (usually both).

Look at the Readme.txt file in the wdog directory
'''

from optparse import OptionParser
import sys
import os
import signal
import logging
import traceback
import socket
import pickle
import commands
from ConfigParser import ConfigParser, NoOptionError,NoSectionError
from base64 import decodestring
from logging.handlers import RotatingFileHandler
from time import sleep, time, strftime, localtime, gmtime, ctime
from smtplib import SMTP
try:
    from md5 import md5   # beware this will be deprecated after python 2.5
except:
    from hashlib import md5
from string import replace, split
from lib.Daemon import daemonize, startstop
from lib.msnbot import launchBot, MSNError

version='0.8'

# constants
SIZE_LOG_FILE = 100000
NB_LOG_FILE = 5
TAIL_PROGRAM = '/usr/bin/tail'
DEFAULT_LASTWRITE = 15*60       # in seconds, of course the period wdog -guard is called should be <= at this one
TMP_FILE_DISKUSE = 'diskCheck.pck'
DISK_ALERT_PERIOD = 3600
# end

try:
    os.stat(os.getcwd()+'/'+sys.argv[0].split('/')[-1])
    wdogDir=os.getcwd()+'/'
except:
    wdogDir=os.path.dirname(__file__)+'/'

wdogConfFile=wdogDir+'wdog.ini'
wdogLogFile=wdogDir+'wdog.log'
wdogLockFile=wdogDir+'wdog.lock'
wconf=None
mylogger=None
isLocked=False

class myLogger(logging.Logger):
    def __init__(self, cfParser):
        r = RotatingFileHandler(wdogDir+'wdog.log', 'a', SIZE_LOG_FILE, NB_LOG_FILE)
        f = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s', '%a, %d %b %Y %H:%M:%S')
        r.setFormatter(f)
        self.l=logging.getLogger('wdog')
        self.l.addHandler(r)
        
	self.isAtty=sys.stderr.isatty()
        if self.isAtty:
            o = logging.StreamHandler()
            o.setFormatter(f)
            self.l.addHandler(o)
        
        self.l.setLevel(logging.DEBUG)
        
        # configure smtp alerting
        self.smtpAlerts=False
        try:
            self.smtpHost=cfParser.get('Alerts', 'smtpHost')
            self.smtpFrom=cfParser.get('Alerts', 'smtpFrom')
            self.smtpSubject=cfParser.get('Alerts', 'smtpSubject')
            self.smtpTo=cfParser.get('Alerts', 'smtpTo')
            self.smtpAlerts=True
            try:
                self.smtpUsername=cfParser.get('Alerts', 'smtpUsername')
                self.smtpPassword=cfParser.get('Alerts', 'smtpPassword')
            except:
                self.smtpUsername=None
        except:
            pass
        
        # configure msn alerting
        self.msnAlerts=False
        try:
            self.msnLogin=cfParser.get('Alerts', 'msnLogin')
            self.msnPassword=cfParser.get('Alerts', 'msnPassword')
            self.msnTo=cfParser.get('Alerts', 'msnTo')
            self.msnAlerts=True
            try:
                self.msnNick=cfParser.get('Alerts', 'msnNick')
            except:
                self.msnNick='WDog'
            try:
                self.msnUserAck=int(cfParser.get('Alerts', 'msnUserAck'))
            except:
                self.msnUserAck=8.5
            try:
                self.msnServerUP=int(cfParser.get('Alerts', 'msnServerUP'))
            except:
                self.msnServerUP=45
        except:
            pass
        
        if self.smtpAlerts or self.msnAlerts:  self.alerts=True
        else: self.alerts=False
        self.pendingMsgs=[]
        self.keepMsgs=False

    def formatExceptionInfo(self, logLevel=logging.INFO, exc_info=None, maxTBlevel=5):
        if not exc_info:
            exc_info = sys.exc_info()
        cla, exc, trbk = exc_info
        if not trbk:
            return
        excName = cla.__name__
        try:
            excArgs = exc.__dict__["args"]
        except KeyError:
            excArgs = "<no args>"
        excTb = traceback.format_tb(trbk, maxTBlevel)
        self.processLog(logLevel, 'Got Exception : '+excName)
        self.processLog(logLevel, str(excArgs))
        self.processLog(logLevel, excTb)
    
    def sendMSN(self, msg, daemon):
        self.l.info("trying to contact %s on MSN", self.msnTo)
        try:
            launchBot(self.msnLogin, self.msnPassword, self.msnNick, self.msnTo, msg, mylogger, self.msnUserAck, self.msnServerUP, daemon)
        except MSNError, e:
            self.l.info(e)
        except:            
            self.l.info("MSN problem reason :")
            self.formatExceptionInfo()
    
    def sendMail(self, msg, daemon):
        self.l.info("trying to send mail from %s to %s", self.smtpFrom, self.smtpTo)
        smtp = SMTP()
        try:
            smtp.connect(self.smtpHost)
            if self.smtpUsername:
                smtp.login(self.smtpUsername, self.smtpPassword)
            uniq_id = md5('%s' % time()).hexdigest()
            
            mailFile = ("From: %s\r\nTo: %s\r\nSubject:%s\r\nMessage-ID: <%s@%s>\r\n\r\n" % (self.smtpFrom, self.smtpTo, self.smtpSubject, uniq_id, socket.getfqdn() ))
            
            # get tail from the log
            if daemon and daemon.tailOutput:
                msg += '\r\n\r\nTail of the log file before the crash:\r\n' + daemon.tailOutput
            smtp.sendmail(self.smtpFrom, self.smtpTo, mailFile + msg)
            smtp.quit()
        except:
            self.formatExceptionInfo(logging.CRITICAL)
    
    def processLog(self, level, msg, *args, **kwargs):
        da = None
        if kwargs.has_key('daemon'):
            da=kwargs['daemon']
            del kwargs['daemon']
        self.l.log(level, msg, *args, **kwargs)
        if self.alerts and level > logging.WARNING:
            # keep pending the msgs
            self.keepMsgs=True
        if self.keepMsgs:
            self.pendingMsgs.append((da, logging.getLevelName(level), msg, args, kwargs))

    def alert(self):
        if self.keepMsgs and not self.isAtty:
            l = []
            for m in self.pendingMsgs:
                l.append('['+m[1]+'] '+m[2])
            msg='\r\n'.join(l)
            if self.smtpAlerts:
                self.sendMail(msg, self.pendingMsgs[0][0])  # beware, this means only one daemon, could be improved
            if self.msnAlerts:
                self.sendMSN(msg, self.pendingMsgs[0][0])   # beware, this means only one daemon, could be improved
        
    def debug(self, msg, *args, **kwargs):
        self.processLog(logging.DEBUG, msg,*args,**kwargs)

    def info(self, msg, *args, **kwargs):
        self.processLog(logging.INFO, msg,*args,**kwargs)

    def warning(self, msg, *args, **kwargs):
        self.processLog(logging.WARNING, msg,*args,**kwargs)

    def error(self, msg, *args, **kwargs):
        self.processLog(logging.ERROR, msg,*args,**kwargs)

    def critical(self, msg, *args, **kwargs):
        self.processLog(logging.CRITICAL, msg,*args,**kwargs)

class Daemon:
    ''' this class represents a daemon as it is described in the wdog.ini '''
    def __init__(self, name, number):
        self.name=name
        self.number=number
        self.commandStr=None
        self.notifiable=None
        self.daemonize=True
        self.logFile=None
        self.logWatch=False
        self.errFile=None      # not implemented yet
        self.errWatch=False    # not implemented yet
        self.pidFile=None
        self.lastWrite=DEFAULT_LASTWRITE
        self.tailOutput=None

    def __str__(self):
        ret=[]
        for k in self.__dict__.keys():
            ret.append(k + ': ' + str(self.__dict__[k]))
        return '\n'.join(ret)

    def getPidFromFile(self):
        '''
        output : the pid found in the pidfile for this daemon or None if any problem
        '''
        if self.pidFile:
            pidFile = self.pidFile
        else:
            pidFile = wdogDir + self.name + '.pid'
        try:
            pf = file(pidFile,'r')
            pid = int(pf.read().strip())
            pf.close()
        except IOError:
            pid = None
        return pid

    def notifyDaemon(self):
        '''
        Do the kill -SIGUSR1 pid for the user.
        ouput : nothing    
        '''
        pid=self.getPidFromFile()
        if not pid:
            self.checkProcessRuns()
        else:
            # check that THIS daemon accepts SIGUSR1
            if not self.notifiable:
                mylogger.critical(self.name+' is started but you didnt activate the possibility to notify it in the ini file.')
                mylogger.info('Check that you really want the daemon \''+self.name+'\' to be notified ( as the default operation for SIGUSR1 is often TERM - platform dependant ) then modify if needed the config file : '+ wdogConfFile+ ' by adding \'notifiable=True\' to section [Daemon'+str(self.number)+']')
            else:
                # ok kill it with SIGUSR1
                mylogger.info('Notifying daemon '+self.name+' with pid '+str(pid)+' and signal SIGUSR1')
                os.kill(pid,signal.SIGUSR1)

    def checkProcessRuns(self):
        '''
        Check if the process was started (pidfile present) and is in memory
        output : nothing
        '''
        mylogger.info('[STATUS of '+self.name+']')
        pid=self.getPidFromFile()
        if not pid:
            mylogger.critical(self.name+' is not started (No pid file found)')
        else:
            mylogger.info('pid file present, pid of '+ self.name + ' is ' + str(pid) + ' according to the file')
            # check it is up and running
            try:
                os.getpgid(pid)
                mylogger.info(str(pid)+' is present in the list of process')
            except OSError:
                # doesnt exist
                mylogger.critical('However pid ' + str(pid) + ' doesnt exist in the list of processes !')
                mylogger.info('You should restart the daemon ' + self.name + ' with :')
                mylogger.info(sys.argv[0] + ' ' + self.name + ' restart    (or with wdog -guard)')
                mylogger.info(' or wait for the guard to restart it if one is set up in CRON')

class Disk:
    ''' keeps information related to partitions '''
    def __init__(self, name, number):
        self.name = name
        self.number = number
        self.dev = None
        self.limit = 95

class ConfWDog:
    '''
    This class represents wdog.ini but in memory.
    '''
    def __init__(self,fileName):
        self._configParser=ConfigParser()
        self.fileName=fileName
        fd=open(self.fileName)
        self._configParser.readfp(fd)
        fd.close()

        self._hostname = socket.getfqdn()
        self.number = 0
        self.daemons = []
        self.disks = []
        n = 0
        while 1:
            section='Daemon'+str(n)
            try:
                sectionNames=self._configParser.options(section)
            except NoSectionError:
                break
            # ok this daemon conf exists
            #print sectionNames
            da=Daemon(self._configParser.get(section,'name'), n)
            da.commandStr=self._configParser.get(section,'command')
            try:
                value=self._configParser.get(section,'notifiable')
                if value[0]=='T' or value=='1':
                    da.notifiable=True
            except:
                pass
            # do wdog have to daemonize it or it does it by itself ?
            try:
                value=self._configParser.get(section,'daemonize')
                if value[0]=='F' or value=='0':
                    da.daemonize=False
            except:
                pass
            if not da.daemonize:
                # must say where is the pid
                da.pidFile=self._configParser.get(section,'pidFile')
            
            # logFile
            try:
                da.logFile=self._configParser.get(section,'logFile')
                # lastWrite
                try:
                    da.lastWrite=int(self._configParser.get(section,'lastWrite'))
                except:
                    pass
            except:
                da.logFile=wdogDir+da.name+'.log'

            # logWatch
            try:
                value=self._configParser.get(section,'logWatch')
                if value[0]=='T' or value=='1':
                    da.logWatch=True
                # lastWrite
                if da.logWatch:
                    try:
                        da.lastWrite=int(self._configParser.get(section,'lastWrite'))
                    except:
                        pass
            except:
                da.logFile=wdogDir+da.name+'.log'
            
            # errFile
            try:
                da.errFile=self._configParser.get(section,'errFile')
            except:
                pass

            # errWatch
            try:
                value=self._configParser.get(section,'errWatch')
                if value[0]=='T' or value=='1':
                    da.errWatch=True
            except:
                pass
                    
            # next one
            self.daemons.append(da)
            n+=1
        
        # now the disks
        n2 = 0
        while 1:
            section='Disk'+str(n2)
            try:
                sectionNames=self._configParser.options(section)
            except NoSectionError:
                break
            # ok this daemon conf exists
            di=Disk(self._configParser.get(section, 'name'), n2)
            di.dev=self._configParser.get(section, 'dev')
            try:
                di.limit=int(self._configParser.get(section, 'limit'))
            except:
                pass
            # next one
            self.disks.append(di)
            n2+=1
        
        if n+n2 == 0:
            raise IOError, fileName + ' not found or not section in it !'

    def getDaemon(self,name):
        ''' give it a daemon_name and it returns either the number of the cell found in the conf or -1 '''
        for da in self.daemons:
            if da.name==name:
                return da
        return None

def WGuard():
    '''
    Main use, this is typically the usage by crontab "wdog.py -guard", it will check all the
    definitions found in the ini file.
    No input and nothing to be returned.
    '''
    sleep(0.02)
    for da in wconf.daemons:
        mylogger.info('Checking '+ da.name + ' :')
        pid=da.getPidFromFile()
        if pid:
            # check it is up and running
            restart=False
            try:
                os.getpgid(pid)
                mylogger.info('  '+ da.name + ' is started and pid = ' + str(pid))
                # check the log files as well if needed
                if da.logWatch:
                    # last modified
                    temps=int(time())
                    try:
                        lastDate=os.stat(da.logFile)[8]
                    except:
                        raise IOError
                    if temps-lastDate > da.lastWrite:
                        # log exists but wasnt modified lately
                        raise IOError
            except IOError:
                # log too old
                mylogger.critical(da.name+ ': the daemon is in memory but seems stuck as the log file was not updated for the last '+str(da.lastWrite/60.0)+' minutes', daemon=da)
                restart=True
            except OSError:
                # pid doesnt exist
                mylogger.critical(da.name+ ': found a pid file but this pid doesnt exist anymore !', daemon=da)
                restart=True
            
            if restart:
                # get tail of the log if it is interesting
                if da.logFile:
                    (_, da.tailOutput)=commands.getstatusoutput(TAIL_PROGRAM + ' ' + da.logFile)
                mylogger.info('Will try to restart daemon '+da.name+' ...')
                (status, output)=commands.getstatusoutput(wdogDir + 'wdog.py '+da.name+' restart')
                if status==0:
                    beg = 'Restart OK'
                else:
                    beg = 'Restart PROBLEM, status = '+str(status)
                mylogger.info(beg + ', output = "'+replace( output, '\n', ' ')+'"' )
        else:
            mylogger.info('  ' + da.name + ' is not started')
    checkDiskSpace()


def getLock():
    """ sets the lock and returns true if ok or false if blocked """
    global isLocked
    try:
        fd = os.open(wdogLockFile, os.O_CREAT|os.O_WRONLY|os.O_EXCL)
        os.write(fd, str(os.getpid()))
        os.close(fd)
    except:
        # file already exists
        fd = file(wdogLockFile, 'r')
        pid = int(fd.read().strip())
        fd.close()
        # is this a restart ?
        if len(sys.argv)>2 and sys.argv[2]=='restart':
            return True
        else:
            # verify the date and if more then 60 secs, assume that 
            # the previous guard crashed before releasing the file
            # (which is really unlikely BUT in case to avoid a deadlock)
            # note that in this case between stat and utime, it isnt atomic.
            temps=int(time())
            lastDate=os.stat(wdogLockFile)[8]
            if temps-lastDate>60:
                # this is my file
                os.utime(wdogLockFile, None)
            else:
                mylogger.warning('Wdog in use, plz wait 60 seconds before try again')
                return False
    isLocked = True
    return True

def releaseLock():
    '''
    Function called at the end of the normal execution to release the lock
    if needed and shutdown properly logging
    '''
    if mylogger:
        #mylogger.formatExceptionInfo(logging.CRITICAL)
        mylogger.alert()
    if isLocked:
        try:
            os.remove(wdogLockFile)
        except:
            pass
    sleep(0.02)
    logging.shutdown()
 
def checkDiskSpace():
    try:
        lastDisks = pickle.load(open(wdogDir + TMP_FILE_DISKUSE))
    except IOError:
        lastDisks = None
    
    # check that the lists are comparable or discard it
    try:
        for i in xrange(len(wconf.disks)):
            if not wconf.disks[i].dev==lastDisks[i].dev:
                raise ValueError
            if not wconf.disks[i].limit==lastDisks[i].limit:
                raise ValueError
    except:
        lastDisks = None
    
    out=commands.getoutput('/bin/df')
    lines=out.split('\n')
    for di in wconf.disks:
        found = 0
        for l in lines:
            if l.startswith(di.dev):
                found = 1
                di.percent = int(l.split('%')[-2].split(' ')[-1])
                di.checked = time()
        if found == 0:
            mylogger.critical('Disk'+str(di.number)+' '+di.name+' '+di.dev+' is not found ! Wdog.ini typo or not mounted !')
        else:
            if di.percent > di.limit:
                 # should warn the user... unless we did already less than an hour ago
                 doAlert = 1
                 if lastDisks:
                     if di.checked - lastDisks[di.number].checked < DISK_ALERT_PERIOD and lastDisks[di.number].percent > di.limit:
                         # avoid spamming too often
                         doAlert = 0
                         di.checked = lastDisks[di.number].checked
                 if doAlert:
                     mylogger.critical('Disk'+str(di.number)+' '+di.name+' '+di.dev+' is now '+str(di.percent)+'% full !')
                 else:
                     mylogger.info('Disk'+str(di.number)+' '+di.name+' '+di.dev+' is now '+str(di.percent)+'% full ! delayed alerting...')
            else:
                 mylogger.info('Disk'+str(di.number)+' '+di.name+' '+di.dev+' usage = '+str(di.percent)+'%')
    
    pickle.dump(wconf.disks, open(wdogDir + TMP_FILE_DISKUSE,'w'))

def main(argv):
    global wconf, mylogger
    sys.exitfunc=releaseLock
    
    def printUsage():
        print 'Usage :'
        print '$ ./wdog.py -guard    <-- manually or by crontab, in this case will trigger alerting'
        print ' or'
        print '$ ./wdog.py name_of_daemon [start|stop|restart|status|notify|tail [-INTEGER]]'
        print
        print 'The config file in use is :'
        print wdogConfFile    
    
    # Usage
    if len(argv)<2 or argv[1]=='-help':
        printUsage()
        return
    
    # Read conf and create logger
    wconf=ConfWDog(wdogConfFile)
    mylogger=myLogger(wconf._configParser)
    ConfWDog.mylogger=mylogger

    # the main use either by crontab or manually
    if argv[1]=='-guard':
        if not getLock():
           return
        WGuard()
        return
    
    # List all the "daemons" entries in the config file
    da=wconf.getDaemon(argv[1])
    if not da:
        mylogger.critical('Didnt find the definition '+argv[1]+' in '+wdogConfFile)
        return

    # tail of the logs
    if len(argv)>2 and argv[2]=='tail':
        log = da.logFile
        if not log:
            log=wdogDir+argv[1]+'.log'
        releaseLock()
        print 'LogFile =', log
        if len(argv)>3:
            os.execl(TAIL_PROGRAM, TAIL_PROGRAM, argv[3], log)
        else:
            os.execl(TAIL_PROGRAM, TAIL_PROGRAM, '-25', log)
        # Never reach here.
    
    if not getLock():
        # do nothing
        return
       
    if len(argv)==2 or argv[2]=='status' or argv[2]=='notify':
        if len(argv)>2 and argv[2]=='notify':
            # do the kill -s SIGUSR1 for me
            da.notifyDaemon()
        else:
            da.checkProcessRuns()
        return

    if len(argv)>2:
        mylogger.info('daemon '+argv[1]+' '+argv[2])
        # is it already a daemon ?
        if not da.daemonize:
            releaseLock()
        else:
            # make a daemon out of it
            startstop(argv[1:], stdout=wdogDir+argv[1]+'.log',stderr=wdogDir+argv[1]+'.err', pidfile=wdogDir+argv[1]+'.pid')
        # we are in the daemon
        arr=split(da.commandStr)
        if not da.daemonize:
            arr.append(argv[2])
        os.execv(arr[0],arr)
        # Never reach here.

    # Unrecognized option
    print 'Unrecognized option'
    printUsage()
    return
    
if __name__ == '__main__':
    main(sys.argv)
