import time
import serial
import threading


class pump_control(threading.Thread):
    def __init__(self) -> None:
        threading.Thread.__init__(self)
         
    def run(self):
        print("pump thread running")
        ser = serial.Serial("com3", 9600, timeout=0.5)
        i = 0
        while True:       # 子进程，用于不断输出0
            if ser.isOpen():
                ser.write('0000'.encode("gbk"))
                # time.sleep(0.001)
                i += 1
            if i == 850:
                break


def pump_on():      #吸泵工作，也就是停止子进程。但是我们不需要自动停止，让它自然继续就可以。
    f = pump_control()
    f.terminate()


def pump_off():     #吸泵停止，也就是开启子进程
    f = pump_control()
    f.start()


if __name__ == '__main__':

    f = pump_control()
    pump_off()
    time.sleep(1)
    