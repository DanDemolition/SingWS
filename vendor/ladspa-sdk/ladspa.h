/* Minimal LADSPA SDK header for building host/plugins locally. */
#ifndef LADSPA_H
#define LADSPA_H

#ifdef __cplusplus
extern "C" {
#endif

#define LADSPA_VERSION "1.1"

typedef float LADSPA_Data;
typedef void *LADSPA_Handle;

typedef unsigned long LADSPA_Properties;
#define LADSPA_PROPERTY_REALTIME                 0x1
#define LADSPA_PROPERTY_INPLACE_BROKEN          0x2
#define LADSPA_PROPERTY_HARD_RT_CAPABLE         0x4

typedef unsigned long LADSPA_PortDescriptor;
#define LADSPA_PORT_INPUT                       0x1
#define LADSPA_PORT_OUTPUT                      0x2
#define LADSPA_PORT_CONTROL                     0x4
#define LADSPA_PORT_AUDIO                       0x8

typedef unsigned long LADSPA_PortRangeHintDescriptor;
#define LADSPA_HINT_BOUNDED_BELOW               0x1
#define LADSPA_HINT_BOUNDED_ABOVE               0x2
#define LADSPA_HINT_TOGGLED                     0x4
#define LADSPA_HINT_SAMPLE_RATE                 0x8
#define LADSPA_HINT_LOGARITHMIC                 0x10
#define LADSPA_HINT_INTEGER                     0x20

#define LADSPA_HINT_DEFAULT_NONE                0x0000
#define LADSPA_HINT_DEFAULT_MINIMUM             0x0040
#define LADSPA_HINT_DEFAULT_LOW                 0x0080
#define LADSPA_HINT_DEFAULT_MIDDLE              0x00C0
#define LADSPA_HINT_DEFAULT_HIGH                0x0100
#define LADSPA_HINT_DEFAULT_MAXIMUM             0x0140
#define LADSPA_HINT_DEFAULT_0                   0x0200
#define LADSPA_HINT_DEFAULT_1                   0x0240
#define LADSPA_HINT_DEFAULT_100                 0x0280
#define LADSPA_HINT_DEFAULT_440                 0x02C0
#define LADSPA_HINT_DEFAULT_MASK                0x03C0

typedef struct _LADSPA_PortRangeHint {
    LADSPA_PortRangeHintDescriptor HintDescriptor;
    LADSPA_Data LowerBound;
    LADSPA_Data UpperBound;
} LADSPA_PortRangeHint;

typedef struct _LADSPA_Descriptor {
    unsigned long UniqueID;
    const char *Label;
    LADSPA_Properties Properties;
    const char *Name;
    const char *Maker;
    const char *Copyright;
    unsigned long PortCount;
    const LADSPA_PortDescriptor *PortDescriptors;
    const char * const *PortNames;
    const LADSPA_PortRangeHint *PortRangeHints;
    void *ImplementationData;
    LADSPA_Handle (*instantiate)(const struct _LADSPA_Descriptor *, unsigned long);
    void (*connect_port)(LADSPA_Handle, unsigned long, LADSPA_Data *);
    void (*activate)(LADSPA_Handle);
    void (*run)(LADSPA_Handle, unsigned long);
    void (*run_adding)(LADSPA_Handle, unsigned long);
    void (*set_run_adding_gain)(LADSPA_Handle, LADSPA_Data);
    void (*deactivate)(LADSPA_Handle);
    void (*cleanup)(LADSPA_Handle);
} LADSPA_Descriptor;

const LADSPA_Descriptor *ladspa_descriptor(unsigned long index);

#ifdef __cplusplus
}
#endif

#endif
