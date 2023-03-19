#!/usr/bin/env python
#
# The MIT License
#
# Copyright (c) 2007 Arluison Guillaume
# http://www.mi-ange.net
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import select
import socket
import time
import sys
import signal
import commands

import msnlib
import msncb

MSG_LENGTH_MAX = 1664   # as of April 2003 see http://www.hypothetic.org/docs/msn/switchboard/messages.php

class MSNError(Exception):
    pass


def loginAlarm(signum, frame):
    ''' alarm handler during login '''
    raise MSNError, "Couldnt log in to MSN"

def completeMSNAlarm(signum, frame):
    ''' alarm handler during whole MSN server '''
    raise MSNError, "MSN server took too long, quit."


def mycb_msg(md, type, tid, params, sbd):
    ''' Get a message callback '''
    message=params.splitlines()[-1]
    if message=='': return
    mail=tid.split()[0]
    md.logger.info(mail+' asked: '+message)
    if message.startswith('df'):
        md.sendMessageBySlice(mail, commands.getoutput('/bin/df -h').replace('\n','\r\n'))
    elif message=='d' or message.startswith('daemon'):
        md.sendMessageBySlice(mail, str(md.daemon))
    elif message.startswith('uptime'):
        md.sendMessageBySlice(mail, commands.getoutput('uptime').replace('\n','\r\n'))
    elif message.startswith('help') or message == 'h':
        md.sendMessageBySlice(mail, 'uptime     gives the uptime\r\ndaemon   to have a list of config variables about the FIRST daemon which crashed\r\ndf                to get the df -h on the machine\r\nquit            to finish with MSN')
    elif message.startswith('quit'):
        md.logger.info('user shutdown server')
        quit(md)
        return
    else:
        pass
    md.logger.info('MSN was used, gracetime pushed 60 seconds more')
    md.nbSecTotal+=60
    secs=int(time.time()-md.start+md.nbSecTotal)
    signal.alarm(secs+5)
    #print 'MESSAGE\n+++ Header: %s\n%s\n\n' % (str(tid), str(params))

def mycb_syn(md, type, tid, params):
    '''
    Receive a SYN notification callback 
    This is called when the msn bot is ready
    '''
    t = params.split()
    if len(t) != 3:
        raise "SYNError"
  
    lver = int(t[0])
    total = int(t[1])
    ngroups = int(t[2])
  
    md.syn_lver = lver
    md.syn_total = total
    md.syn_ngroups = ngroups
    
    md.sendMessageBySlice(md.initMsg[0], md.initMsg[1]+'\r\n\r\n   type h for help')

def mycb_ack(md, type, tid, params, nd):
    ''' Receive a ACK notification callback '''
    if not md.connectionEstablished:
        # this is the ack of the first msg sent to the user
        md.connectionEstablished=1

def sendMessageBySlice(self, email, msg='', sb=None):
    lng = len(msg)
    if lng >= MSG_LENGTH_MAX:
        lines = msg.splitlines()
        lines.reverse()
        m2 = []
        lng2 = 0
        while len(lines)>0:
            tmp = len(lines[-1])
            if lng2 + tmp >= MSG_LENGTH_MAX-164:
                # ok send the first packet
                msgtmp = '\r\n'.join(m2)
                self.sendmsg(email, msgtmp, sb)
                lng2 = 0
                m2 = []
            else:
                lng2 += tmp + 2
                m2.append(lines.pop())
        if m2 != []:
            self.sendmsg(email, '\r\n'.join(m2), sb)
    else:
        self.sendmsg(email, msg, sb)


def noop(s): pass

class noclass: pass

def echo(s):
    print s

def quit(md):
    try:
        md.disconnect()
    except:
        pass

    md.continueRun=0
    signal.alarm(0)


def launchBot(login, password, nickname, sendTo, msg, logger, nbSecACK, nbSecTotal, daemon=None):
    msnlib.msnd.sendMessageBySlice = sendMessageBySlice
    m = msnlib.msnd()
    m.cb = msncb.cb()
    m.cb.msg = mycb_msg
    m.cb.syn = mycb_syn
    m.cb.ack = mycb_ack
    msnlib.debug=noop
    msncb.debug=noop
    m.email=login
    m.pwd=password
    m.initMsg = ( sendTo, msg )
    if not logger:
        m.logger=noclass()
        m.logger.info=echo
    else:
        m.logger = logger
    m.nbSecTotal = nbSecTotal
    m.continueRun=1
    m.connectionEstablished=0
    m.daemon=daemon
    
    # Set the signal handler and a 5-second alarm
    signal.signal(signal.SIGALRM, loginAlarm)
    signal.alarm(15)
    m.login()
    m.sync()
    signal.alarm(0)
    signal.signal(signal.SIGALRM, completeMSNAlarm)
    m.start=time.time()
    signal.alarm(nbSecTotal+5)
    
    m.change_nick(nickname)
    m.change_status("online")
    
    while m.continueRun:
        # we get pollable fds
        t = m.pollable()
        infd = t[0]
        outfd = t[1]
      
        # we select, waiting for events
        try:
            fds = select.select(infd, outfd, [], 0)
        except:
            quit(m)
        
        for i in fds[0] + fds[1]:       # see msnlib.msnd.pollable.__doc__
            try:
                m.read(i)
            except ('SocketError', socket.error), err:
                if i != m:
                    # user closed a connection
                    # note that messages can be lost here
                    m.close(i)
                else:
                    # main socket closed
                    quit(m)
      
        # sleep a bit so we don't take over the cpu
        time.sleep(0.1)
        now=time.time()
        if not m.connectionEstablished and now-m.start >= nbSecACK:
            m.logger.info(sendTo + ' seems not available, dropping MSN')
            quit(m)
        if now-m.start >= m.nbSecTotal:
            msg = 'Maximum waiting time on MSN, log off'
            m.sendMessageBySlice(sendTo, msg)
            m.logger.info(msg)
            quit(m)

if __name__ == '__main__':
   launchBot('botmsnaccount@example.com', 'password', '<WDogTest>', 'usermsnaccount@example.com', 'this is a test', None, 8.5, 30)