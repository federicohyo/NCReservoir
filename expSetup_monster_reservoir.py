'''
 Copyright (C) 2014 - Federico Corradi
 Copyright (C) 2014 - Juan Pablo Carbajal
 
 This progrm is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 3 of the License, or
 (at your option) any later version.

 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with this program. If not, see <http://www.gnu.org/licenses/>.
'''

############### author ##########
# federico corradi
# federico@ini.phys.ethz.ch
# Juan Pablo Carbajal 
# ajuanpi+dev@gmail.com
#
# ===============================
#!/usr/env/python
from __future__ import division

import numpy as np
from pylab import *
import pyNCS
import sys
import matplotlib
sys.path.append('../api/lsm/')
sys.path.append('../api/retina/')
sys.path.append('../gui/reservoir_display/')
import lsm as L
import time
import retina as ret

######################################
# Configure chip
try:
  is_configured
except NameError:
  print "Configuring chip"
  is_configured = False
else:
  print "Chip is configured: ", is_configured

save_data_to_disk = True
real_time_learn = True
produce_learning_plot = True
test_reservoir = False
teach_orth = True

if (is_configured == False):



    #populations divisible by 2 for encoders
    neuron_ids = np.linspace(0,254,255)
    nsync = 255
    ncol_retina_sync = 5
    npops      = len(neuron_ids)

    #setup
    prefix    = '../'
    setuptype = '../setupfiles/mc_final_mn256r1.xml'
    setupfile = '../setupfiles/final_mn256r1_retina_monster.xml'
    nsetup    = pyNCS.NeuroSetup(setuptype, setupfile, prefix=prefix)
    nsetup.mapper._init_fpga_mapper()

    chip      = nsetup.chips['mn256r1']

    chip.configurator._set_multiplexer(0)

    #populate neurons
    rcnpop = pyNCS.Population('neurons', 'for fun') 
    rcnpop.populate_by_id(nsetup,'mn256r1','excitatory', neuron_ids)


    #init liquid state machine
    print "################# init onchip recurrent connections... [reservoir] "
    liquid = L.Lsm(rcnpop, cee=0.7, cii=0.3)

    #c = 0.2
    #dim = np.round(np.sqrt(len(liquid.rcn.synapses['virtual_exc'].addr)*c))

    use_retina_projections = (liquid.matrix_programmable_rec != 1)
    liquid.matrix_programmable_exc_inh[use_retina_projections] = np.random.choice([0,1], size=np.sum(use_retina_projections))
    liquid.matrix_programmable_w[use_retina_projections] = np.random.choice([0,1,2,3], size=np.sum(use_retina_projections))
    
    liquid.program_config()
    ###### configure retina
    inputpop = pyNCS.Population('','')
    inputpop.populate_by_id(nsetup,'mn256r1', 'excitatory', np.linspace(0,255,256))  
    syncpop = pyNCS.Population("","")
    syncpop.populate_by_id(nsetup,'mn256r1', 'excitatory', [nsync])
    #reset multiplexer
    chip.configurator._set_multiplexer(0)
    retina = ret.Retina(inputpop)
    retina._init_fpga_mapper()
    retina.map_retina_sync(syncpop, ncol_retina = ncol_retina_sync, neu_sync=nsync) ### neuron 255 is our sync neuron
    rr, pre, post  = retina.map_retina_random_connectivity(inputpop, c = 0.3, syntype='virtual_exc', ncol_sync = ncol_retina_sync)
    rr, pre, post  = retina.map_retina_random_connectivity(inputpop, c = 0.1, syntype='virtual_inh', ncol_sync = ncol_retina_sync)

    import RetinaInputs as ri
    win = ri.RetinaInputs(nsetup)
    #win.run(300)

    chip.load_parameters('biases/biases_reservoir_retina.biases')

    # do config only once
    is_configured = True
  
  
# End chip configuration
######################################

######################################
# Generate gestures parameters

nx_d = 16
ny_d = 16

######################################


# Number of time steps to sample the mean rates
nsteps = 50
######################################

# Function to calculate region of activity
func_avg = lambda t,ts: np.exp((-(t-ts)**2)/(2*500**2)) # time in ms

# Handle to figure to plot while learning
#fig_h = figure()
#fig_i = figure()
ion()

# Store scores of RC
scores = []
tot_scores_in = []
tot_scores_out = []
# Stimulation parameters
duration   = 20000   #ms
delay_sync = 500
framerate = 120
counts = (duration/1000)*framerate #for 60 frames per sec
factor_input = 10 # 100
n_neu = 256

# Time vector for analog signals
Fs    = 100/1e3 # Sampling frequency (in kHz)
T     = duration+delay_sync+1000
nT    = np.round (Fs*T)
timev = np.linspace(0,T,nT)

#Conversion from spikes to analog
membrane = lambda t,ts: np.atleast_2d(np.exp((-(t-ts)**2)/(2*500**2)))

liquid.RC_reset()
ac = []
syn_per_input_neu = 6
c = syn_per_input_neu/len(liquid.rcn.synapses['virtual_exc'].addr)


## convert retina inputs
def convert_input(inputs):
    '''
    convert 128*128 to 256 ids with macro pixels
    '''
    retina_pixels = 2**14
    num_square = 256
    pixels_per_square = np.floor(retina_pixels/num_square)
    index_list = []
    retina = np.zeros([128,128])
    final_inputs = np.copy(inputs)
    for i in range(16):
        for j in range(16):
            this_square = 0.1*i+0.33*j+0.0537
            index_list.append(this_square)
            retina[j*int(np.sqrt(pixels_per_square)):(j+1)*int(np.sqrt(pixels_per_square)),i*int(np.sqrt(pixels_per_square)):(i+1)*int(np.sqrt(pixels_per_square))] = this_square
    for i in range(256):
        x , y = np.where(retina == index_list[i])    
        indexes = np.ravel_multi_index([x,y], dims=(128,128))
        if(len(indexes) > 0):
            tf = np.in1d(inputs[0][:,1],indexes)#tf,un = _ismember(inputs[0][:,1],indexes)
            final_inputs[0][tf,1] = i
            
    return final_inputs
        
        
def train(num_gestures,ntrials):
    '''
    train reservoir with retina inputs
    '''
    for ind in range(num_gestures):

        print "sample omegas from a 3d sphere surface"
        #N = np.random.randint(10000)
        #v = np.random.uniform(size=(3,N)) 
        #vn = v / np.sqrt(np.sum(v**2, 0))
        #omegas = [vn[0,N-1], vn[1,N-1], vn[2,N-1]]
        theta = np.linspace(0,2.0*pi,num_gestures)
        phi  = np.linspace(0,pi/10.0,350) #bigger smaller angle
        T,P = np.meshgrid(theta,phi)
        wX = sin(P)*cos(T)
        wY = sin(P)*sin(T) 
        wZ = cos(P)
        omegas = [wX[ntrials-1,ind], wY[ntrials-1,ind], wZ[ntrials-1,ind]]
        #print "this gesture's omega ", omegas

        #just poke it for the first time... to start in same configuration all the times..
        inputs, outputs = win.run(np.round(nT/10), framerate=framerate, neu_sync = nsync, omegas = omegas)    
        
        for this_t in xrange(ntrials): 
            #omegas = [wX[ind,this_t]+0.5, wY[ind,this_t], wZ[ind,this_t]]
            omegas = [wX[ind,this_t]+0.5, 0.0, 1.0]
            print "this gesture's omega ", omegas
        
            inputs, outputs = win.run(nT, framerate=framerate, neu_sync = nsync, omegas = omegas)        
            #from 6x3 input signals to matrix of nTx256 signals
            #X = np.zeros([nT,n_neu])
            #a,b,c = np.shape(win.inputs_signals)
            #X[:,0:b*c] = np.reshape(win.inputs_signals, [nT,b*c])       
            d,e,f = np.shape(win.teach_signals)
            teach_sign = np.zeros([nT,n_neu])
            teach_sign[:,0:e*f] = np.reshape(win.teach_signals, [nT,e*f])    
            
            
            # Convert input and output spikes to analog signals
            if real_time_learn:
                #inputs = convert_input(inputs)
                #X = L.ts2sig(timev, membrane, inputs[0][:,0], inputs[0][:,1], n_neu = 256)
                X = np.zeros([nT,n_neu])
                a,b,c = np.shape(win.inputs_signals)
                X[:,0:b*c] = np.reshape(win.inputs_signals, [nT,b*c])  

                #learn.. what? tip or something ortogonal to it?                
                if teach_orth:
                    teach_ortogonal_sign = L.orth_signal(X)#np.zeros([nT,n_neu])
                    teach_ortogonal = np.zeros([nT,n_neu])
                    for i in range(n_neu):
                        teach_ortogonal[:,i] = teach_ortogonal_sign
                    teach_sign = teach_ortogonal
                    
                Y = L.ts2sig(timev, membrane, outputs[0][:,0], outputs[0][:,1], n_neu = 256)
                
                print "teaching non orthogonal stuff to the input signals..."
                liquid._realtime_learn (X,Y,teach_sign)
                print np.sum(liquid.CovMatrix['input']), np.sum(liquid.CovMatrix['output'])
                #evaluate
                zh = liquid.RC_predict (X,Y)
                score_in = liquid.RC_score(zh["input"], teach_sign)
                score_out = liquid.RC_score(zh["output"], teach_sign)
                tot_scores_in.append(score_in[0:e*f,:])
                tot_scores_out.append(score_out[0:e*f,:])
                #print "we are scoring...", scores                 
                    
            if save_data_to_disk:
                print "save data"        
                np.savetxt("lsm_ret/inputs_gesture_"+str(ind)+"_trial_"+str(this_t)+".txt", inputs[0])
                np.savetxt("lsm_ret/outputs_gesture_"+str(ind)+"_trial_"+str(this_t)+".txt", outputs[0])
                for i in range(e*f):
                    np.savetxt("lsm_ret/teaching_signals_gesture_"+str(ind)+"_teach_input_"+str(i)+"_trial_"+str(this_t)+".txt", teach_sign[:,i])

 
    return X, Y, teach_sign, zh


def test(omegas=[0.5,0.0,1.0]):
    '''
    test reservoir on an unseen combination of omegas
    '''
    #predict
    tot_in_scores = np.zeros([nT,n_neu, 2])
    tot_out_scores = np.zeros([nT,n_neu, 2])

    print "this gesture's omega ", omegas
    inputs, outputs = win.run(nT, framerate=framerate, omegas = omegas)  
         
    d,e,f = np.shape(win.teach_signals)
    teach_sign = np.zeros([nT,n_neu])
    teach_sign[:,0:e*f] = np.reshape(win.teach_signals, [nT,e*f])       
             
    #scaling patching in 256
    #from 6x3 input signals to matrix of nTx256 signals
    #X = np.zeros([nT,n_neu])
    #a,b,c = np.shape(win.inputs_signals)
    #X[:,0:b*c] = np.reshape(win.inputs_signals, [nT,b*c])
        

    # Convert input and output spikes to analog signals
    inputs = convert_input(inputs)
    X = L.ts2sig(timev, membrane, inputs[0][:,0], inputs[0][:,1], n_neu = 256)
    Y = L.ts2sig(timev, membrane, outputs[0][:,0], outputs[0][:,1], n_neu = 256)
    zh = liquid.RC_predict (X,Y)
    #pred_in_scores = liquid.RC_score(zh["input"], teach_sign)
    #pred_out_scores = liquid.RC_score(zh["output"], teach_sign)
    
    return X, Y, teach_sign, zh

################################################
# TRAINING RESERVOIR
################################################
num_gestures = 10 # Number of gestures
ntrials      = 3 # Number of repetitions of each gesture
X, Y, teach_sign, zh = train(num_gestures,ntrials)#train(num_gestures,ntrials)

if produce_learning_plot:
    figure()
    title('training error')
    zh = liquid.RC_predict (X,Y)
    clf()
    for i in range(6):
        subplot(1,6,i+1)
        title('training error')
        plot(timev,teach_sign[:,i],label='teach signal')
        plot(timev,zh["input"][:,i], label='input')
        plot(timev,zh["output"][:,i], label='output')
    legend(loc='best')

    figure()
    for i in range(3):
        subplot(3,2,i+1)
        title('training error')
        a = i*2
        b = i*2+1 
        plot(teach_sign[:,a],teach_sign[:,b], label='target')
        plot(zh["input"][:,a],zh["input"][:,b], label='inputs')
        plot(zh["output"][:,a],zh["output"][:,b], label='outputs')
    
################################################
# TESTING RESERVOIR
################################################   
if test_reservoir: 
    omegas = [0.5, 0, 1.0]      
    X,Y, teach_sign, zh = test(omegas=omegas)
                 
    figure()
    zh = liquid.RC_predict (X,Y)
    clf()
    for i in range(6):
        subplot(1,6,i+1)
        title('testing error')
        plot(timev,teach_sign[:,i],label='teach signal')
        plot(timev,zh["input"][:,i], label='input')
        plot(timev,zh["output"][:,i], label='output')
    legend(loc='best')


    figure()
    for i in range(3):
        subplot(3,2,i+1)
        title('testing error')
        a = i*2
        b = i*2+1 
        plot(teach_sign[:,a],teach_sign[:,b], label='target')
        plot(zh["input"][:,a],zh["input"][:,b], label='inputs')
        plot(zh["output"][:,a],zh["output"][:,b], label='outputs')
        

########################################################
# USEFUL FUNCTIONS TO DEBUG AND TEST
#######################################################
#fig_h = figure()
def test_determinism():
    inputs, outputs = win.run(nT, framerate=framerate, neu_sync = nsync, omegas = omegas) 
    Y = L.ts2sig(timev, membrane, outputs[0][:,0], outputs[0][:,1], n_neu = 256)
    print "we are plotting outputs"
    figure(fig_h.number)
    for i in range(256):
        subplot(16,16,i)
        plot(Y[:,i])
        axis('off')       
        
def _ismember( a, b):
    '''
    as matlab: ismember
    '''
    # tf = np.in1d(a,b) # for newer versions of numpy
    tf = np.array([i in b for i in a])
    u = np.unique(a[tf])
    index = np.array([(np.where(b == i))[0][-1] if t else 0 for i,t in zip(a,tf)])
    return tf, index        
                
def plot_inputs(inputs):                
    ret_image = np.zeros([128,128])
    ret_image = ret_image.flatten()

    for i in range(len(inputs[0][:,0])):
        ret_image[int(inputs[0][i,1])] += 1 
        
    imshow(np.fliplr(ret_image.reshape(128,128).T))
    colorbar()
        
def plot_inputs_small(inputs):                
    ret_image = np.zeros([16,16])
    ret_image = ret_image.flatten()

    for i in range(len(inputs[0][:,0])):
        ret_image[int(inputs[0][i,1])] += 1 
        
    imshow(np.fliplr(ret_image.reshape(16,16).T), interpolation='nearest')
    colorbar()
        
