# -*- coding: utf-8 -*-
# Copyright (c) 2014, Vispy Development Team.
# Distributed under the (new) BSD License. See LICENSE.txt for more info.

from __future__ import division

from .. import gloo
from ..util import event
from ..shaders.composite import ModularProgram
from .transforms import NullTransform


"""
API Issues to work out:

  * Need Visual.bounds() as described here:
    https://github.com/vispy/vispy/issues/141

  - Should have swappable input component to allow a variety of different 
    vertex inputs:
        2d attribute + z uniform
        3d attribute
        2d attribute + z uniform + index
        3d attribute + index
        1d attribute + x/y ranges (surface plot)
        (and any other custom input component the user might come up with)
        
  - Should have swappable / chainable fragment components:
        Per-vertex normals (for smooth surfaces)
        Per-face normals (for faceted surfaces)
        Colors per-vertex, per-face
        Materials - phong, etc.
        Textures - color, bump map, spec map, etc
        Wireframe rendering (note this might require vertex modification)
        
  - For efficiency, the vertex inputs should allow both pre-index and 
    unindexed arrays. However, many fragment shaders may require pre-indexed
    arrays. For example, drawing faceted surfaces is not possible with 
    unindexed arrays since the normal vector changes each time a vertex is
    visited.
        => this means that input components need a way to convert their data
           and suggest a different input component (?)
        => More generally, we need to be able to map out all of the available
           pathways and choose the most efficient one based on the input
           data format (to avoid unnecessary conversions) and the requirements
           of individual components (including indexed/unindexed, whether
           geometry shaders are available, ...)
    
  - Fragment shaders that do not need normals should obviously not compute them
  
  - Some materials require a normal vector, but there may be any number of
    ways to generate a normal: per-vertex interpolated, per-face, bump maps,
    etc. This means we need a way for one material to indicate that it requires
    normals, and a way to tell the component which normal-generating component
    it should use.
        => Likewise with colors. In fact, normals and colors are similar enough
           that they could probably share most of the same machinery..
           

    => Color chain   \
                      ===>  Material chain
    => Normal chain  /

    Examples:
        Color input / filters:
            uniform color
            color by vertex, color by face
            texture color
            float texture + colormap
            color by height
            grid contours
            wireframe
        
        Normal input:
            normal per vertex
            normal per face
            texture bump map
            texture normal map
        
        Material composition:
            shaded / facets
            shaded / smooth
            phong shading

"""



# Commonly-used GL option groups.
GLOptions = {
    'opaque': {
        'GL_DEPTH_TEST': True,
        'GL_BLEND': False,
        #'GL_ALPHA_TEST': False,
        'GL_CULL_FACE': False,
    },
    'translucent': {
        'GL_DEPTH_TEST': True,
        'GL_BLEND': True,
        #'GL_ALPHA_TEST': False,
        'GL_CULL_FACE': False,
        'glBlendFunc': ('GL_SRC_ALPHA', 'GL_ONE_MINUS_SRC_ALPHA'),
    },
    'additive': {
        'GL_DEPTH_TEST': False,
        'GL_BLEND': True,
        #'GL_ALPHA_TEST': False,
        'GL_CULL_FACE': False,
        'glBlendFunc': ('GL_SRC_ALPHA', 'GL_ONE'),
    },
}    


class Visual(object):
    """ 
    Abstract class representing a drawable object. Visuals implement the 
    following interfaces:
    
        * paint() calls all of the GL commands necessary to paint the visual.
        * bounds() describes the bounding rectangle of the visual.
        * gl_options() is used to configure the OpenGL state immediately
          before the visual is painted.
          
    
    Events:
    
    update : Event
        Emitted when the visual has changed and needs to be repainted.
    bounds_change : Event
        Emitted when the bounding rectangle of the visual has changed.
    """
    
    VERTEX_SHADER = """
    // local_position function must return the current vertex position
    // in the Visual's local coordinate system.
    vec4 local_position();

    // mapping function that transforms from the Visual's local coordinate
    // system to normalized device coordinates.
    vec4 map_local_to_nd(vec4);

    // generic hook for executing code after the vertex position has been set
    void vert_post_hook();

    // Global variable storing the results of local_position()
    // Any component may read this variable, but it should be treated as
    // read-only.
    vec4 local_pos;

    void main(void) {
        local_pos = local_position();
        vec4 nd_pos = map_local_to_nd(local_pos);
        gl_Position = nd_pos;
        
        vert_post_hook();
    }
    """

    FRAGMENT_SHADER = """
    // Fragment shader consists of only a single hook that is usually defined 
    // by a chain of functions, each which sets or modifies the current fragment
    // color, or discards it.
    vec4 frag_color();

    void main(void) {
        gl_FragColor = frag_color();
    }
    """

    
    
    def __init__(self):
        
        # Dict of {'GL_FLAG': bool} and {'glFunctionName': (args)} 
        # specifications. By default, these are enabled whenever the Visual 
        # if painted. This provides a simple way for the user to customize the
        # appearance of the Visual. Example:
        # 
        #     { 'GL_BLEND': True,
        #       'glBlendFunc': ('GL_SRC_ALPHA', 'GL_ONE') }
        # 
        self._gl_options = {}
        
        self.events = event.EmitterGroup(source=self,
                                         update=event.Event,
                                         bounds_change=event.Event)

        self._program = ModularProgram(self.VERTEX_SHADER, 
                                       self.FRAGMENT_SHADER)
        
        self.transform = NullTransform()
        
        # Generic chains for attaching post-processing functions
        self._program.add_chain('vert_post_hook')
        self._program.add_chain('frag_color')
        
        # Components for plugging different types of position and color input.
        self._pos_component = None
        self._color_component = None
        #self.pos_component = XYZPosComponent()
        self._frag_components = []
        #self.fragment_components = [UniformColorComponent()]

    @property
    def primitive(self):
        """
        The GL primitive used to draw this visual.
        """
        return gloo.gl.GL_TRIANGLES
    
    @property
    def vertex_index(self):
        """
        Returns the IndexBuffer (or None) that should be used when drawing 
        this Visual.        
        """
        return self.pos_component.index
        
    def set_gl_options(self, default=None, **opts):
        """
        Set all GL options for this Visual. 
        Keyword arguments must be one of two formats:
        
        * GL_FLAG=bool
        * glFunctionName=(args)
        
        These options are invoked every time the Visual is drawn.
        Optionally, *default* gives the name of a pre-set collection of options
        from the GLOptions global.
        """
        if default is not None:
            opts = GLOptions[default]
        self._gl_options = opts
        
    def update_gl_options(self, default=None, **opts):
        """
        Update GL options rather than replacing all. See set_gl_options().
        
        Optionally, *default* gives the name of a pre-set collection of options
        from the GLOptions global.
        """
        if default is not None:
            opts = GLOptions[default]
        self._gl_options.update(opts)
        
    def gl_options(self):
        """
        Return a dict describing the GL options in use for this Visual. 
        See set_gl_options().
        """
        return self._gl_options.copy()

    @property
    def pos_component(self):
        return self._pos_component

    @pos_component.setter
    def pos_component(self, component):
        if self._pos_component is not None:
            self._pos_component._detach(self)
            self._pos_component = None
        component._attach(self)
        self._pos_component = component
        self.events.update()
        
    @property
    def fragment_components(self):
        return self._frag_components[:]
    
    @fragment_components.setter
    def fragment_components(self, comps):
        for comp in self._frag_components:
            comp._detach()
        self._frag_components = comps
        for comp in self._frag_components:
            comp._attach(self)
        self.events.update()

    def update(self):
        """
        This method is called whenever the Visual must be repainted.
        
        """
        self.events.update()
    
    def paint(self):
        """
        Paint this visual now.
        
        The default implementation configures GL flags according to the contents
        of self._gl_options            
        
        """
        self._activate_gl_options()
        mode = self._paint_mode()
        self._activate_components(mode)
        self._program.draw(self.primitive, self.vertex_index)

    def _paint_mode(self):
        """
        Return the mode that should be used to paint this visual
        (DRAW_PRE_INDEXED or DRAW_UNINDEXED)
        """
        modes = set(self.pos_component.supported_draw_modes)
        for comp in self._frag_components:
            modes &= set(comp.supported_draw_modes)
        
        if len(modes) == 0:
            for c in self._frag_components:
                print(c, c.supported_draw_modes)
            raise Exception("Visual cannot paint--no mutually supported "
                            "draw modes between components.")
        
        #TODO: pick most efficient draw mode!
        return list(modes)[0]
    
    def _activate_gl_options(self):
        for name, val in self._gl_options.items():
            if isinstance(val, bool):
                flag = getattr(gloo.gl, name)
                if val:
                    gloo.gl.glEnable(flag)
                else:
                    gloo.gl.glDisable(flag)
            else:
                args = []
                for arg in val:
                    if isinstance(arg, str):
                        arg = getattr(gloo.gl, arg)
                    args.append(arg)
                func = getattr(gloo.gl, name)
                func(*args)
                
    def _activate_components(self, mode):
        # activate all components
        self.pos_component.activate(self._program, mode)
        for comp in self._frag_components:
            comp.activate(self._program, mode)
        
        # TODO: this must be optimized.
        self._program['map_local_to_nd'] = self.transform.shader_map()



class VisualComponent(object):
    """
    Base for classes that encapsulate some modular component of a Visual.
    
    These define Functions for extending the shader code as well as an 
    activate() method that inserts these Functions into a program.
    
    VisualComponents may be considered friends of the Visual they are attached
    to; often they will need to access internal data structures of the Visual
    to make decisions about constructing shader components.
    """
    
    DRAW_PRE_INDEXED = 1
    DRAW_UNINDEXED = 2
    
    def __init__(self, visual=None):
        self._visual = None
        if visual is not None:
            self._attach(visual)
        
    @property
    def visual(self):
        """The Visual that this component is attached to."""
        return self._visual

    def _attach(self, visual):
        """Attach this component to a Visual. This should be called by the 
        Visual itself.
        """
        self._visual = visual

    def _detach(self):
        """Detach this component from its Visual.
        """
        self._visual = None
        
    @property
    def supported_draw_modes(self):
        """
        A tuple of the draw modes (either DRAW_PRE_INDEXED, DRAW_UNINDEXED, or
        both) currently supported by this component.
        
        DRAW_PRE_INDEXED indicates that the component may be used when the 
        program uses an array of indexes do determine the order of elements to
        draw from its vertex buffers (using glDrawElements).
        
        DRAW_UNINDEXED indicates that the component may be used when the
        program will not use an array of indexes; rather, vertex buffers are
        processed in the order they appear in the buffer (using glDrawArrays).
        
        By default, this method returns a tuple with both values. Components 
        that only support one mode must override this method.
        """
        # TODO: This should be expanded to include other questions, such as
        # whether the visual supports geometry shaders.
        return DRAW_PRE_INDEXED, DRAW_UNINDEXED

    def update(self):
        """
        Inform the attached visual that this component has changed.
        """
        if self.visual is not None:
            self.visual.update()

    def activate(self, program):
        """
        *program* is about to paint; attach to *program* all functions and 
        data required by this component.
        """
        raise NotImplementedError
