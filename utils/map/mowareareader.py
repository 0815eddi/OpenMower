############################################################################################################################
# mowareareader for openmower
# a simple map editor based on maptolib
# v0.1 nekraus
# v0.2 javaboon
# v0.3 eddi 20230423
#  - changes: switch from polygon PathPatch
#  - obstacle integration
# v0.4 eddi (thanks to dvismans!!)
#  - possibility of deleting points (Note (first and last points of a circle MUST NOT be deleted!)
#  - some basic "how to use" - instructions 
# state: x,y-points in one ore more mow or navigation areas including 0 to n obstacles can be moved/deleted and saved to a new map (output.bag)
# v0.5 suplstfdo: Add map background image to plot
#
# to do:
#
# - possibilty of adding points 
# - integration of docking station
# you need a python runtime and at least bagpy-package (pip3 install bagpy)
# copy your map.bag from openmower (/root/ros_home/.ros/map.bag) in the same directory where mowareareader.py is running
# When running a window opens with your 1st mow area (including obstacles). You can move and delete the points as you like. then close the window.
# When you close the window, the next mow area is shown - and so on. 
# After the mow areas the navigations areas will follow step by step. Handle them in the same way.
# Output is saved in the same directory as "output.bag"
# copy output.bag to your openmower (/root/ros_home/.ros/map.bag)
# You can enhance the experience by adding a graphical map to the plot. This can be a screenshot from google maps of your lawn. Name the picture 'yard.png'
# and place id beside the script. rename ".env-example" to just ".env" and alter the variables according to your map. The "GRAPHICAL_MAP_PX_PER_M" must
# be be measured from your map. Google map shows a scale at the bottom right corner, which is helpful for this. The "GRAPHICAL_MAP_ORIGIN_PX" values are the pixel
# coordinates of the png, that shows your map origin, aka. "OM_DATUM_*" values you configured for your openmower.
#
# KEEP A COPY OF YOUR ORIGIN MAP.BAG!!! - for the case something is wrong with your new map you can go back to lat working map
###############################################################################################################################


import sys
import ntpath
import os
import time
from io import BytesIO
import csv
import inspect
import rosbag
from std_msgs.msg import String, Header
from geometry_msgs.msg  import Point32, Pose, PoseStamped
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.artist import Artist
from matplotlib.path import Path
from matplotlib.backend_bases import MouseButton
import matplotlib.image as mpimg
import dotenv

dotenv.load_dotenv()

def dist(x, y):
    """
    Return the distance between two points.
    """
    d = x - y
    return np.sqrt(np.dot(d, d))


def dist_point_to_segment(p, s0, s1):
    """
    Get the distance of a point to a segment.
      *p*, *s0*, *s1* are *xy* sequences
    This algorithm from
    http://www.geomalgorithms.com/algorithms.html
    """
    v = s1 - s0
    w = p - s0
    c1 = np.dot(w, v)
    if c1 <= 0:
        return dist(p, s0)
    c2 = np.dot(v, v)
    if c2 <= c1:
        return dist(p, s1)
    b = c1 / c2
    pb = s0 + b * v
    return dist(p, pb)


class PathInteractor:
    """
    A path editor.

    Press 't' to toggle vertex markers on and off.  When vertex markers are on,
    they can be dragged with the mouse.
    """

    showverts = True
    epsilon = 25  # max pixel distance to count as a vertex hit

    def __init__(self, pathpatch):

        self.ax = pathpatch.axes
        canvas = self.ax.figure.canvas
        self.pathpatch = pathpatch
        self.pathpatch.set_animated(True)

        x, y = zip(*self.pathpatch.get_path().vertices)

        self.line, = ax.plot(
            x, y, marker='o', markerfacecolor='r', animated=True)

        self._ind = None  # the active vertex

        canvas.mpl_connect('draw_event', self.on_draw)
        canvas.mpl_connect('button_press_event', self.on_button_press)
        canvas.mpl_connect('key_press_event', self.on_key_press)
        canvas.mpl_connect('button_release_event', self.on_button_release)
        canvas.mpl_connect('motion_notify_event', self.on_mouse_move)
        self.canvas = canvas

    def get_ind_under_point(self, event):
        """
        Return the index of the point closest to the event position or *None*
        if no point is within ``self.epsilon`` to the event position.
        """
        xy = self.pathpatch.get_path().vertices
        xyt = self.pathpatch.get_transform().transform(xy)  # to display coords
        xt, yt = xyt[:, 0], xyt[:, 1]
        d = np.sqrt((xt - event.x)**2 + (yt - event.y)**2)
        ind = d.argmin()
        return ind if d[ind] < self.epsilon else None

    def on_draw(self, event):
        """Callback for draws."""
        self.background = self.canvas.copy_from_bbox(self.ax.bbox)
        self.ax.draw_artist(self.pathpatch)
        self.ax.draw_artist(self.line)
        self.canvas.blit(self.ax.bbox)

    def on_button_press(self, event):
        """Callback for mouse button presses."""
        if (event.inaxes is None
                or event.button != MouseButton.LEFT
                or not self.showverts):
            return
        self._ind = self.get_ind_under_point(event)

    def on_button_release(self, event):
        """Callback for mouse button releases."""
        if (event.button != MouseButton.LEFT
                or not self.showverts):
            return
        self._ind = None

    def on_key_press(self, event):
        #Callback for key presses.
        if not event.inaxes:
            return
        if event.key == 't':
            self.showverts = not self.showverts
            self.line.set_visible(self.showverts)
            if not self.showverts:
                self._ind = None
        elif event.key == 'd':
            ind = self.get_ind_under_point(event)
            if ind is not None:
                path_data = self.pathpatch.get_path()
                path_data.vertices = np.delete(path_data.vertices, ind, axis=0)
                path_data.codes = np.delete(path_data.codes, ind, axis=0)
                if ind == 0:
                    path_data.codes[0] = Path.MOVETO
                elif ind == len(path_data.codes):
                    path_data.codes[-1] = Path.CLOSEPOLY
                self.pathpatch.set_path(path_data)
                self.line.set_data(zip(*self.pathpatch.get_path().vertices))
        self.canvas.draw()

    def on_mouse_move(self, event):
        """Callback for mouse movements."""
        if (self._ind is None
                or event.inaxes is None
                or event.button != MouseButton.LEFT
                or not self.showverts):
            return

        vertices = self.pathpatch.get_path().vertices

        vertices[self._ind] = event.xdata, event.ydata
        self.line.set_data(zip(*vertices))

        self.canvas.restore_region(self.background)
        self.ax.draw_artist(self.pathpatch)
        self.ax.draw_artist(self.line)
        self.canvas.blit(self.ax.bbox)

if __name__ == '__main__':
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.patches import PathPatch
    
    
    bag = rosbag.Bag('map.bag')
    with rosbag.Bag('output.bag', 'w') as outbag:
        for topic, msg, t in bag.read_messages():
            if (topic == 'mowing_areas' or topic == 'navigation_areas'):
                x_list_area = []
                y_list_area = []
                verts=[]
                codes=[]

                for point in msg.area.points:
                    x_list_area.append(point.x)
                    y_list_area.append(point.y)

                    x=[]

                code=""
                i = 0
                n = len(x_list_area)
                
                verts = (np.column_stack([x_list_area,y_list_area]))
                while i < n:
                    if i == 0:
                        code=Path.MOVETO
                    elif i == n-1:
                        code=Path.CLOSEPOLY
                    else:
                        code=Path.LINETO    

                    codes.append(code)
                    
                    i = i + 1
                path = Path(verts, codes)               
                o = len(msg.obstacles)
                for k in range(o): #Loop over 0 - n obstacles

                        x_list_obstacle=[]
                        y_list_obstacle=[]
                        verts_o=[]
                        codes_o=[]               
                        for point in msg.obstacles[k].points:

                             x_list_obstacle.append(point.x)
                             y_list_obstacle.append(point.y) 
                            
                             j = 0
                        n = len(x_list_obstacle)
                        
                        verts_o = (np.column_stack([x_list_obstacle,y_list_obstacle]))
                        while j < n:
                                    #print (j) 
                                    if j == 0:
                                     code=Path.MOVETO
                                    elif j == n-1:
                                         code=Path.CLOSEPOLY
                                    else:
                                        code=Path.LINETO    
                                    codes_o.append(code)
                                    j = j + 1
                        path_o=Path(verts_o, codes_o)
                        path=Path.make_compound_path(path,path_o)       
 
                fig, ax = plt.subplots()
                if os.environ.get('GRAPHICAL_MAP_FILENAME') and os.environ.get('GRAPHICAL_MAP_PX_PER_M') and \
                os.environ.get('GRAPHICAL_MAP_ORIGIN_PX_X') and os.environ.get('GRAPHICAL_MAP_ORIGIN_PX_Y'):
                    img = mpimg.imread(os.environ['GRAPHICAL_MAP_FILENAME'])
                    px_per_m=float(os.environ['GRAPHICAL_MAP_PX_PER_M'])
                    m_per_px=1/px_per_m
                    null_punkt_x = int(os.environ['GRAPHICAL_MAP_ORIGIN_PX_X'])
                    null_punkt_y = int(os.environ['GRAPHICAL_MAP_ORIGIN_PX_Y'])
                    width_px, height_px, _ = img.shape
                    image_scale = [
                        -null_punkt_x*m_per_px,
                        (width_px-null_punkt_x)*m_per_px,
                        -null_punkt_y*m_per_px,
                        (height_px-null_punkt_y)*m_per_px
                    ]
                    imageshow = ax.imshow(img, extent=image_scale, aspect='equal')
                patch = PathPatch(path, facecolor='white', edgecolor='black', alpha=0.5)
                ax.add_patch(patch)
                
            
                p = PathInteractor(patch)
                fig.canvas.manager.set_window_title('OpenMower Map editor')
                ax.set_title('Click and drag a point to move it')
                ax.set_xlabel( "\'d\' delete the point     \'i\' insert a new point", size=12, ha="center")
                ax.set_xlim((min(x_list_area)-3, max(x_list_area)+3))
                ax.set_ylim((min(y_list_area)-3, max(y_list_area)+3))
                
                plt.show()
                #figure closed by user, get the new data
                msg.area.points = []
                msg_area=msg.area.points
                l = 0   #counter elements in path
                m = -1  #counter obstacles (0 - n), mowing area= -1 

                for x,y in p.pathpatch.get_path().vertices:
   
                    point = Point32(x,y,0) 
                    msg_area.append(point)
                  
                    #change msg_area on CLOSEPOLY (code 79)
                    if p.pathpatch.get_path().codes[l] == 79:                           
                        if m < o - 1:   
                             m = m + 1
                             msg.obstacles[m].points=[]
                             msg_area=msg.obstacles[m].points
                    l = l + 1
            outbag.write(topic, msg, t)
            





