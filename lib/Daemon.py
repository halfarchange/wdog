#!/usr/bin/env python

import sys, os, time
from signal import SIGTERM, SIGKILL

def daemonize(stdout='/dev/null', stderr=None, stdin='/dev/null', pidfile=None, startmsg = 'started with pid %s' ):
    '''
    This forks the current process into a daemon.
    The stdin, stdout, and stderr arguments are file names that
    will be opened and be used to replace the standard file descriptors
    in sys.stdin, sys.stdout, and sys.stderr.
    These arguments are optional and default to /dev/null.
    Note that stderr is opened unbuffered, so
    if it shares a file with stdout then interleaved output
    may not appear in the order that you expect.
    '''
    # Do first fork.
    try: 
        pid = os.fork() 
        if pid > 0: sys.exit(0) # Exit first parent.
    except OSError, e: 
        sys.stderr.write("fork #1 failed: (%d) %s\n" % (e.errno, e.strerror))
        sys.exit(1)
        
    # Decouple from parent environment.
    os.chdir("/") 
    os.umask(0) 
    os.setsid() 
    
    # Do second fork.
    try: 
        pid = os.fork() 
        if pid > 0: os._exit(0) # Exit second parent.
    except OSError, e: 
        sys.stderr.write("fork #2 failed: (%d) %s\n" % (e.errno, e.strerror))
        os._exit(1)
    
    # Open file descriptors and print start message
    if not stderr: stderr = stdout
    si = file(stdin, 'r')
    # note that these files must have ABSOLUTE paths
    so = file(stdout, 'a+')
    se = file(stderr, 'a+', 0)
    pid = str(os.getpid())
    sys.stderr.write("%s\n" % startmsg % pid)
    sys.stderr.flush()
    if pidfile: file(pidfile,'w+').write("%s\n" % pid)
    
    # Redirect standard file descriptors.
    os.dup2(si.fileno(), sys.stdin.fileno())
    os.dup2(so.fileno(), sys.stdout.fileno())
    os.dup2(se.fileno(), sys.stderr.fileno())

def startstop(argv=None, stdout='/dev/null', stderr=None, stdin='/dev/null', pidfile='pid.txt', startmsg = 'started with pid %s' ):
    '''
    function to call "before" the main in your program, it will
    automatically take care of the start/stop mechanism and the rest
    of the code will be run in daemon mode    
    '''
    if not argv: argv = sys.argv
    if len(argv) > 1:
        action = argv[1]
        try:
            pf  = file(pidfile,'r')
            pid = int(pf.read().strip())
            pf.close()
        except IOError:
            pid = None
        if 'stop' == action or 'restart' == action:
            if not pid:
                mess = "Could not stop, pid file '%s' missing.\n"
                sys.stderr.write(mess % pidfile)
                if 'stop' == action:
                    sys.exit(1)
                action = 'start'
                pid = None
            else:
               try:
                  for i in xrange(3):
                      os.kill(pid,SIGTERM)
                      time.sleep(1)
                  os.kill(pid,SIGKILL)
                  time.sleep(1)
                  # Note that this code was not tested ! It's tricky to crash so badly a daemon
                  # that it doesnt answer to a kill -9
                  try:
                      os.getpgid(pid)
                      sys.stderr.write('Error kill -9 didnt achieve to kill pid '+str(pid))
                  except OSError:
                      # ok SIGKILL killed it
                      pass
               except OSError, err:
                  err = str(err)
                  if err.find("No such process") > 0:
                      os.remove(pidfile)
                      if 'stop' == action:
                          sys.exit(0)
                      action = 'start'
                      pid = None
                  else:
                      sys.stderr.write(str(err))
                      sys.exit(1)
        if 'start' == action:
            if pid:
                mess = "Start aborted since pid file '%s' exists.\n"
                sys.stderr.write(mess % pidfile)
                sys.exit(1)
            daemonize(stdout,stderr,stdin,pidfile,startmsg)
            return
    print "usage: %s start|stop|restart" % argv[0]
    sys.exit(2)

def getPidFromFile(pidfile):
    '''
    input : fileName
    output : the pid found in the pidfile for this daemon or None if any problem
    '''
    try:
        pf = file(pidfile,'r')
        pid = int(pf.read().strip())
        pf.close()
    except IOError:
        pid = None
    return pid

def test():
    '''
    This is an example if this module is runt directly.
    This prints a count and timestamp once per second as a daemon.
    '''
    import signal, os
    valeur=[0]

    def handler(signum, frame):
        sys.stdout.write ('I noticed the signal !\n')
        sys.stdout.flush()
        valeur[0]=1

    signal.signal(signal.SIGUSR1, handler)

    sys.stdout.write ('Message to stdout...')
    sys.stderr.write ('Message to stderr...')
    c = 0
    while 1:
        sys.stdout.write ('%d: %s\n' % (c, time.ctime(time.time())) )
        sys.stdout.flush()
        c = c + 1
        time.sleep(1)
        if valeur[0]==1:
            return

if __name__ == "__main__":
    startstop(stdout='/tmp/deamonize.log',stderr='/tmp/deamonize.err', pidfile='/tmp/deamonize.pid')
    # we are in the daemon now
    print 'Look in the /tmp dir'
    print 'Dont forget to stop the daemon'
    test()
