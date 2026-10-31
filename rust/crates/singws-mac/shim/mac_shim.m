// Thin Objective-C shim for singws-mac. Plain C ABI; every returned string is malloc'd and must be released with singws_mac_free.
// Nothing here ever shows a permission prompt: Accessibility is checked with the prompt option OFF, and location only READS the
// authorization status unless it is already authorized.
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <CoreLocation/CoreLocation.h>
#include <stdlib.h>
#include <string.h>

static char *dup_ns(NSString *s) { return strdup(s ? [s UTF8String] : ""); }

void singws_mac_free(char *p) { free(p); }

// ---- keep awake: same options as mac_keep_awake.py -------------------------------------------------------------------------------
static id g_activity = nil;
static NSActivityOptions keep_awake_options(void) {
    NSActivityOptions idle_display = (NSActivityOptions)1 << 40;
    NSActivityOptions idle_system = (NSActivityOptions)1 << 20;
    return (NSActivityOptions)(0x00FFFFFFULL | idle_system) | idle_system | idle_display;
}
int singws_mac_keep_awake_begin(const char *reason) {
    @autoreleasepool {
        if (g_activity != nil) return 1;
        NSString *why = reason && *reason ? [NSString stringWithUTF8String:reason] : @"SingWS session";
        g_activity = [[NSProcessInfo processInfo] beginActivityWithOptions:keep_awake_options() reason:why];
        return g_activity != nil;
    }
}
void singws_mac_keep_awake_end(void) {
    @autoreleasepool {
        id token = g_activity;
        g_activity = nil;
        if (token != nil) [[NSProcessInfo processInfo] endActivity:token];
    }
}
int singws_mac_keep_awake_active(void) { return g_activity != nil; }

// ---- frontmost application (localized name, like NSWorkspace in the app) ---------------------------------------------------------
char *singws_mac_frontmost_app_name(void) {
    @autoreleasepool {
        NSRunningApplication *app = [[NSWorkspace sharedWorkspace] frontmostApplication];
        return dup_ns(app.localizedName);
    }
}

// ---- Accessibility trust, never prompting ----------------------------------------------------------------------------------------
int singws_mac_ax_is_trusted(void) {
    @autoreleasepool {
        NSDictionary *opts = @{ (__bridge NSString *)kAXTrustedCheckOptionPrompt : @NO };
        return AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)opts) ? 1 : 0;
    }
}

// ---- AppleScript, in this process (so the Automation/Accessibility grant belongs to the host binary, like NSAppleScript in the app) --
int singws_mac_run_applescript(const char *source, char **out, char **err) {
    @autoreleasepool {
        *out = NULL; *err = NULL;
        NSAppleScript *script = [[NSAppleScript alloc] initWithSource:[NSString stringWithUTF8String:source ? source : ""]];
        if (script == nil) { *err = strdup("could not compile script source"); return 0; }
        NSDictionary *error = nil;
        NSAppleEventDescriptor *result = [script executeAndReturnError:&error];
        if (error != nil) { *err = dup_ns([error description]); return 0; }
        *out = dup_ns(result.stringValue);
        return 1;
    }
}

// ---- location ----------------------------------------------------------------------------------------------------------------------
@interface SWSLoc : NSObject <CLLocationManagerDelegate>
@property (nonatomic, strong) CLLocation *fix;
@property (nonatomic, strong) NSError *error;
@end
@implementation SWSLoc
- (void)locationManager:(CLLocationManager *)m didUpdateLocations:(NSArray<CLLocation *> *)locs { self.fix = locs.lastObject; }
- (void)locationManager:(CLLocationManager *)m didFailWithError:(NSError *)e { self.error = e; }
@end

// kCLAuthorizationStatus: 0 not determined, 1 restricted, 2 denied, 3 authorized (always), 4 authorized when in use.
int singws_mac_location_status(void) {
    @autoreleasepool {
        if (![CLLocationManager locationServicesEnabled]) return -1;
        CLLocationManager *m = [[CLLocationManager alloc] init];
        return (int)m.authorizationStatus;
    }
}

// Returns 1 with a fix, 0 with *err set. Declines (0, "not authorized: N") without ever requesting authorization.
int singws_mac_location_once(double timeout_s, double *lat, double *lon, double *acc, char **err) {
    @autoreleasepool {
        *err = NULL;
        int status = singws_mac_location_status();
        if (status < 0) { *err = strdup("location services disabled"); return 0; }
        if (status != 3 && status != 4) {
            char buf[64]; snprintf(buf, sizeof buf, "not authorized: status %d", status);
            *err = strdup(buf);
            return 0;
        }
        SWSLoc *delegate = [[SWSLoc alloc] init];
        CLLocationManager *m = [[CLLocationManager alloc] init];
        m.delegate = delegate;
        [m requestLocation];
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:timeout_s];
        while (delegate.fix == nil && delegate.error == nil && [deadline timeIntervalSinceNow] > 0)
            [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
        if (delegate.fix != nil) {
            *lat = delegate.fix.coordinate.latitude; *lon = delegate.fix.coordinate.longitude; *acc = delegate.fix.horizontalAccuracy;
            return 1;
        }
        *err = delegate.error ? dup_ns([delegate.error description]) : strdup("timeout");
        return 0;
    }
}
